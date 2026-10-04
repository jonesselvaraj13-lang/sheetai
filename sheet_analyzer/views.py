import io
import json
import os
import re
import pandas as pd
from django.shortcuts import render, get_object_or_404, redirect
from django.http import HttpResponse, JsonResponse
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from .forms import FileUploadForm, ProfilePhotoForm
from .models import UploadedFile, Profile
from .utils import read_file, read_url, read_url_text, compare_datasets, process_prompt, format_analysis_result, analyze_data_quality, clean_noise_data, clean_text_noise, _read_pdf_text, pdf_text_query, generate_file_insight, generate_business_insights, generate_text_insights, generate_file_important_details, generate_text_important_details, render_text_answer_html
from docx import Document as DocxDocument


def login_view(request):
    if request.user.is_authenticated:
        return redirect('upload_query')
    error = None
    if request.method == 'POST':
        login_id = request.POST.get('username', '').strip()
        password = request.POST.get('password', '')
        username = login_id
        if '@' in login_id:
            from django.contrib.auth.models import User
            matched_user = User.objects.filter(email__iexact=login_id).first()
            if matched_user:
                username = matched_user.username
        user = authenticate(request, username=username, password=password)
        if user is not None:
            login(request, user)
            return redirect('upload_query')
        else:
            error = 'Invalid username/email or password. Please try again.'
    return render(request, 'login.html', {'error': error})


def logout_view(request):
    logout(request)
    return redirect('login')


def _render_text_analysis(text, source_name, prompt, title='Business Insights'):
    display_prompt = prompt or 'Summarize this content'
    answer = pdf_text_query(text, display_prompt)
    return format_analysis_result(
        analysis_text=render_text_answer_html(answer),
        data_preview_html='',
        filename=source_name,
        prompt=display_prompt,
        business_insights_html=generate_text_insights(text, display_prompt),
        insights_title=title,
        important_details_html=generate_text_important_details(text, source_name, display_prompt),
    )


def _is_wikipedia_url(url):
    return bool(url and 'wikipedia.org' in url.lower())


@login_required
def upload_and_query(request):
    data = None
    quality_data = None
    compare_data = None
    viewed_record = None
    has_active_source = False
    form = FileUploadForm()

    if request.method == 'POST':
        record_id = request.POST.get('record_id')
        prompt = request.POST.get('prompt', '').strip()
        url1_raw = request.POST.get('url_link', '').strip()
        url2_raw = request.POST.get('url_link_2', '').strip()

        if record_id and not url1_raw and not url2_raw and 'file' not in request.FILES and 'file2' not in request.FILES:
            try:
                old_record = UploadedFile.objects.get(pk=record_id)
                if old_record.url_link:
                    url1_raw = old_record.url_link
                if old_record.url_link_2:
                    url2_raw = old_record.url_link_2
            except UploadedFile.DoesNotExist:
                old_record = None
        else:
            old_record = None

        if not url1_raw:
            prompt_url = re.search(r'https?://\S+|(?:www\.)?[A-Za-z0-9.-]+\.[A-Za-z]{2,}(?:/\S*)?', prompt)
            if prompt_url:
                url1_raw = prompt_url.group(0).rstrip('.,;)')
                prompt = prompt.replace(prompt_url.group(0), '').strip() or 'Summarize this URL'

        # Auto-fix URL scheme if missing
        if url1_raw and not url1_raw.startswith(('http://', 'https://')):
            url1_raw = 'https://' + url1_raw
        if url2_raw and not url2_raw.startswith(('http://', 'https://')):
            url2_raw = 'https://' + url2_raw

        # Inject fixed URLs back so form validates
        post_data = request.POST.copy()
        post_data['url_link'] = url1_raw
        post_data['url_link_2'] = url2_raw
        form = FileUploadForm(post_data, request.FILES)

        # Always process — form.is_valid() is overridden to always return True
        form.is_valid()
        try:
            instance = form.save(commit=False)
        except Exception:
            instance = UploadedFile()
            if 'file' in request.FILES:
                instance.file = request.FILES['file']
            if 'file2' in request.FILES:
                instance.file2 = request.FILES['file2']

        if old_record and 'file' not in request.FILES and 'file2' not in request.FILES:
            if old_record.file and old_record.file.name:
                instance.file = old_record.file
            if old_record.file2 and old_record.file2.name:
                instance.file2 = old_record.file2
            if old_record.url_link and not url1_raw:
                url1_raw = old_record.url_link
            if old_record.url_link_2 and not url2_raw:
                url2_raw = old_record.url_link_2

        instance.prompt = prompt
        instance.url_link = url1_raw
        instance.url_link_2 = url2_raw
        if url1_raw and not url2_raw and 'file2' not in request.FILES:
            # A pasted URL is the active single source. Ignore any stale file
            # value the browser may keep from a previous upload.
            instance.file = None
        instance.save()
        viewed_record = instance

        if True:  # always process

            url1 = instance.url_link.strip() if instance.url_link else ''
            url2 = instance.url_link_2.strip() if instance.url_link_2 else ''
            has_file1 = bool(instance.file and instance.file.name)
            has_file2 = bool(instance.file2 and instance.file2.name)
            has_active_source = bool(url1 or url2 or has_file1 or has_file2)

            # Resolve source 1
            df1, name1, raw_text1 = None, None, None
            if url1:
                df1 = read_url(url1)
                name1 = url1
                # Always fetch raw webpage text for URLs so prompts can query page content
                raw_text1 = read_url_text(url1)
            elif has_file1:
                df1 = read_file(instance.file.path)
                name1 = instance.file.name.split('/')[-1]

            # Resolve source 2
            df2, name2 = None, None
            if has_file2:
                df2 = read_file(instance.file2.path)
                name2 = instance.file2.name.split('/')[-1]
            elif url2:
                df2 = read_url(url2)
                name2 = url2
            # COMPARE MODE: two sources
            if df1 is not None and df2 is not None:
                if hasattr(df1, 'head') and hasattr(df2, 'head'):
                    summary_df, col_df, common_cols, only1, only2, prompt_result = compare_datasets(df1, df2, name1, name2, prompt)
                    compare_data = {
                        'summary_html': summary_df.to_html(classes='data-table', index=False),
                        'column_html': col_df.to_html(classes='data-table', index=False) if col_df is not None else '',
                        'common_count': len(common_cols),
                        'only_file': only1,
                        'only_url': only2,
                        'file_name': name1,
                        'url_name': name2,
                        'prompt_result': prompt_result,
                        'prompt': prompt,
                    }
                else:
                    text_parts = []
                    if isinstance(df1, str):
                        text_parts.append(f'Source 1 ({name1})\n{df1}')
                    elif hasattr(df1, 'head'):
                        text_parts.append(f'Source 1 ({name1})\n{df1.head(50).to_string(index=False)}')
                    if isinstance(df2, str):
                        text_parts.append(f'Source 2 ({name2})\n{df2}')
                    elif hasattr(df2, 'head'):
                        text_parts.append(f'Source 2 ({name2})\n{df2.head(50).to_string(index=False)}')
                    compare_prompt = prompt or 'Compare these two sources and summarize the key differences and similarities.'
                    data = _render_text_analysis(
                        '\n\n'.join(text_parts),
                        f'{name1} vs {name2}',
                        compare_prompt,
                        title='Comparison Insights',
                    )

            # SINGLE SOURCE MODE
            elif df1 is not None or raw_text1:
                df, source_name = df1, name1
                # Extract raw text for PDF/DOCX file or URL webpage
                raw_text = raw_text1
                if has_file1 and not raw_text:
                    ext = os.path.splitext(instance.file.path)[1].lower()
                    if ext == '.pdf':
                        raw_text = _read_pdf_text(instance.file.path)
                    elif ext == '.docx':
                        try:
                            doc = DocxDocument(instance.file.path)
                            raw_text = '\n'.join([p.text for p in doc.paragraphs])
                        except Exception:
                            pass

                # URL with no parseable table — answer from webpage text
                if raw_text and (df is None or isinstance(df, str) or not hasattr(df, 'head')):
                    data = _render_text_analysis(
                        raw_text,
                        source_name,
                        prompt or ('Summarize this Wikipedia article' if _is_wikipedia_url(url1) else 'Summarize this URL'),
                        title='URL Insights' if source_name and str(source_name).startswith(('http://', 'https://')) else 'File Insights',
                    )
                
                # URL returned a table AND prompt given — answer from raw text for better results
                elif isinstance(df, str):
                    is_url_error = source_name and str(source_name).startswith(('http://', 'https://')) and (
                        df.startswith('Error reading URL:') or df.startswith('Could not parse data from URL')
                    )
                    answer = df if is_url_error else (pdf_text_query(df, prompt) if prompt else df)
                    data = format_analysis_result(
                        analysis_text='<pre style="white-space:pre-wrap">' + answer + '</pre>',
                        data_preview_html='' if is_url_error else '<pre>' + df[:500] + ('...' if len(df) > 500 else '') + '</pre>',
                        filename=source_name, prompt=prompt
                    )
                elif hasattr(df, 'head'):
                    quality_data = analyze_data_quality(df)
                    if prompt:
                        fp = instance.file.path if has_file1 else None
                        table_raw_text = raw_text if _is_wikipedia_url(url1) else None
                        result = process_prompt(df, prompt, raw_text=table_raw_text, file_path=fp)
                        business_insights_html = generate_business_insights(df)
                        if isinstance(result, str) and result == '__INSIGHT__':
                            from .utils import generate_file_insight
                            analysis_html = generate_file_insight(df)
                            business_insights_html = ''
                        elif isinstance(result, str):
                            analysis_html = render_text_answer_html(result)
                        elif list(result.columns) == ['Answer'] and len(result) == 1:
                            analysis_html = render_text_answer_html(str(result.iloc[0]['Answer']))
                        else:
                            result = result.dropna(axis=1, how='all')
                            result.columns = [str(c).replace('\n', ' ').strip() for c in result.columns]
                            if result.empty:
                                analysis_html = '<pre>No matching rows found for your query.</pre>'
                            else:
                                analysis_html = result.to_html(classes='data-table', index=False)
                        data = format_analysis_result(
                            analysis_text=analysis_html,
                            data_preview_html='',
                            filename=source_name, prompt=prompt,
                            business_insights_html=business_insights_html,
                            insights_title='Business Insights',
                            important_details_html=generate_file_important_details(df, source_name),
                        )
                    else:
                        # No prompt: show complete file insight
                        data = format_analysis_result(
                            analysis_text=generate_file_insight(df, include_business=False),
                            data_preview_html=df.head(10).dropna(axis=1, how='all').to_html(classes='data-table', index=False),
                            filename=source_name, prompt='Complete File Insight',
                            business_insights_html=generate_business_insights(df),
                            insights_title='Business Insights',
                            important_details_html=generate_file_important_details(df, source_name),
                        )
                else:
                    data = format_analysis_result(analysis_text='<pre>' + str(df) + '</pre>', data_preview_html='<pre>' + str(df) + '</pre>', filename=source_name, prompt=prompt)
            else:
                if url1:
                    data = format_analysis_result(
                        analysis_text=(
                            '<pre style="white-space:pre-wrap">'
                            'I received the URL, but I could not extract readable content from it.\n\n'
                            'Please check that the URL is public and starts with http:// or https://.\n'
                            'Some pages block automated reading; for Wikipedia, paste the full article URL from the browser address bar.'
                            '</pre>'
                        ),
                        data_preview_html='',
                        filename=url1,
                        prompt=prompt or 'URL Analysis'
                    )
                else:
                    data = '<pre>Please upload a file or paste a URL in the URL field.</pre>'

    history = UploadedFile.objects.all().order_by('-uploaded_at')[:20]
    return render(request, 'index.html', {
        'form': form,
        'data': data,
        'history': history,
        'quality_data': quality_data,
        'compare_data': compare_data,
        'viewed_record': viewed_record,
        'has_active_source': has_active_source,
    })


@login_required
def clean_data(request, pk):
    record = get_object_or_404(UploadedFile, pk=pk)
    try:
        # URL-only records have no file
        if not record.file or not record.file.name:
            return HttpResponse('Clean Noise is only available for uploaded files, not URLs.', status=400)
        if not os.path.exists(record.file.path):
            return HttpResponse('File not found on server.', status=404)
        ext = os.path.splitext(record.file.path)[1].lower()
        df = read_file(record.file.path)

        if isinstance(df, str):
            return HttpResponse(f'Cannot clean file: {df}', status=400)
        if not hasattr(df, 'head'):
            return HttpResponse('This file type cannot be cleaned. Only CSV/XLSX/PDF/DOCX files are supported.', status=400)

        before_rows = len(df)
        if ext in ['.pdf', '.docx']:
            cleaned_df = clean_text_noise(df)
        else:
            cleaned_df = clean_noise_data(df)
        after_rows = len(cleaned_df)

        buffer = io.StringIO()
        cleaned_df.to_csv(buffer, index=False)
        buffer.seek(0)

        filename = 'cleaned_' + record.filename()
        response = HttpResponse(buffer.getvalue(), content_type='text/csv')
        response['Content-Disposition'] = 'attachment; filename="' + filename + '"'
        response['X-Rows-Before'] = str(before_rows)
        response['X-Rows-After'] = str(after_rows)
        return response
    except Exception as e:
        return HttpResponse('Error cleaning file: ' + str(e), status=500)


def _safe_delete_file(file_field):
    """Delete a file safely — skip if locked by another process (Windows)."""
    if not file_field or not file_field.name:
        return
    try:
        path = file_field.path
        if os.path.exists(path):
            os.remove(path)
    except (PermissionError, OSError):
        pass  # file locked on Windows — skip, DB record still deleted


@login_required
def delete_record(request, pk):
    record = get_object_or_404(UploadedFile, pk=pk)
    _safe_delete_file(record.file)
    _safe_delete_file(record.file2)
    record.delete()
    return redirect('upload_query')


@login_required
def clear_all_history(request):
    if request.method == 'POST':
        for record in UploadedFile.objects.all():
            _safe_delete_file(record.file)
            _safe_delete_file(record.file2)
        UploadedFile.objects.all().delete()
    return redirect('upload_query')


@login_required
def view_record(request, pk):
    record = get_object_or_404(UploadedFile, pk=pk)
    data = None
    quality_data = None
    try:
        if not record.file or not record.file.name:
            if record.url_link:
                raw_text = read_url_text(record.url_link)
                df = read_url(record.url_link)
                if raw_text:
                    data = _render_text_analysis(
                        raw_text,
                        record.url_link,
                        'Summarize this URL',
                        title='URL Insights',
                    )
                elif hasattr(df, 'head'):
                    quality_data = analyze_data_quality(df)
                    data = format_analysis_result(
                        analysis_text=generate_file_insight(df, include_business=False),
                        data_preview_html=df.head(10).dropna(axis=1, how='all').to_html(classes='data-table', index=False),
                        filename=record.url_link, prompt='Complete File Insight',
                        business_insights_html=generate_business_insights(df),
                        insights_title='Business Insights',
                        important_details_html=generate_file_important_details(df, record.url_link))
                else:
                    data = format_analysis_result(
                        analysis_text='<pre>This URL could not be reloaded. Please paste it again to analyze.</pre>',
                        data_preview_html='', filename=record.url_link, prompt='URL Analysis')
            else:
                data = format_analysis_result(
                    analysis_text='<pre>This record has no file or URL to view.</pre>',
                    data_preview_html='', filename='', prompt='View Record')
        elif not os.path.exists(record.file.path):
            data = format_analysis_result(
                analysis_text='<pre>File no longer exists on server.</pre>',
                data_preview_html='', filename=record.filename(), prompt='View Record')
        else:
            df = read_file(record.file.path)
            if isinstance(df, str):
                data = _render_text_analysis(
                    df,
                    record.file.name,
                    'Summarize this file',
                    title='File Insights',
                )
            elif hasattr(df, 'head'):
                quality_data = analyze_data_quality(df)
                data = format_analysis_result(
                    analysis_text=generate_file_insight(df, include_business=False),
                    data_preview_html=df.head(10).dropna(axis=1, how='all').to_html(classes='data-table', index=False),
                    filename=record.file.name, prompt='Complete File Insight',
                    business_insights_html=generate_business_insights(df),
                    insights_title='Business Insights',
                    important_details_html=generate_file_important_details(df, record.file.name))
            else:
                data = format_analysis_result(
                    analysis_text='<pre>' + str(df) + '</pre>',
                    data_preview_html='<pre>' + str(df) + '</pre>',
                    filename=record.file.name, prompt='View Record')
    except Exception as e:
        data = '<pre>Error reading file: ' + str(e) + '</pre>'

    history = UploadedFile.objects.all().order_by('-uploaded_at')[:20]
    return render(request, 'index.html', {
        'form': FileUploadForm(),
        'data': data,
        'history': history,
        'viewed_record': record,
        'quality_data': quality_data,
        'has_active_source': bool(
            (record.file and record.file.name) or
            (record.file2 and record.file2.name) or
            record.url_link or
            record.url_link_2
        ),
    })


@login_required
def custom_admin(request):
    """Custom admin panel — superusers only."""
    if not request.user.is_superuser:
        return HttpResponse('<h2>403 Forbidden</h2><p>Superuser access required.</p>', status=403)
    from django.contrib.auth.models import User
    users   = User.objects.all().order_by('date_joined')
    records = UploadedFile.objects.all().order_by('-uploaded_at')
    msg = request.GET.get('msg', '')
    return render(request, 'custom_admin.html', {
        'users': users, 'records': records, 'msg': msg,
        'total_users': users.count(),
        'total_records': records.count(),
    })


@login_required
def admin_create_user(request):
    if not request.user.is_superuser:
        return HttpResponse('403 Forbidden', status=403)
    if request.method == 'POST':
        from django.contrib.auth.models import User
        username = request.POST.get('username', '').strip()
        email = request.POST.get('email', '').strip()
        password = request.POST.get('password', '').strip()
        is_super = request.POST.get('is_superuser') == 'on'
        if not username or not password:
            return redirect('/panel/?msg=Username and password required.')
        if User.objects.filter(username=username).exists():
            return redirect('/panel/?msg=Username already exists.')
        if email and User.objects.filter(email__iexact=email).exists():
            return redirect('/panel/?msg=Email already exists.')
        if is_super:
            User.objects.create_superuser(username=username, password=password, email=email)
        else:
            User.objects.create_user(username=username, password=password, email=email)
        return redirect('/panel/?msg=User created successfully.')
    return redirect('/panel/')


@login_required
def admin_delete_user(request, uid):
    if not request.user.is_superuser:
        return HttpResponse('403 Forbidden', status=403)
    from django.contrib.auth.models import User
    if request.user.id == uid:
        return redirect('/panel/?msg=Cannot delete your own account.')
    User.objects.filter(pk=uid).delete()
    return redirect('/panel/?msg=User deleted.')


@login_required
def admin_delete_record(request, pk):
    if not request.user.is_superuser:
        return HttpResponse('403 Forbidden', status=403)
    record = get_object_or_404(UploadedFile, pk=pk)
    _safe_delete_file(record.file)
    _safe_delete_file(record.file2)
    record.delete()
    return redirect('/panel/?msg=Record deleted.')


@login_required
def edit_data(request, pk):
    record = get_object_or_404(UploadedFile, pk=pk)
    if not os.path.exists(record.file.path):
        return HttpResponse('File not found.', status=404)
    df = read_file(record.file.path)
    if not hasattr(df, 'head'):
        return HttpResponse('Only CSV/XLSX files can be manually edited.', status=400)
    if request.method == 'POST':
        try:
            payload = json.loads(request.body)
            row_idx  = int(payload['row'])
            col_name = payload['col']
            new_value = payload['value']
            if col_name not in df.columns:
                return JsonResponse({'error': 'Invalid column'}, status=400)
            if pd.api.types.is_numeric_dtype(df[col_name]):
                try:
                    new_value = float(new_value)
                except ValueError:
                    return JsonResponse({'error': 'Expected a numeric value for this column'}, status=400)
            df.at[row_idx, col_name] = new_value
            ext = os.path.splitext(record.file.path)[1].lower()
            if ext == '.csv':
                df.to_csv(record.file.path, index=False)
            else:
                df.to_excel(record.file.path, index=False)
            return JsonResponse({'success': True, 'value': str(new_value)})
        except Exception as e:
            return JsonResponse({'error': str(e)}, status=500)
    columns = list(df.columns)
    rows = df.values.tolist()
    return render(request, 'edit.html', {
        'record': record, 'columns': columns, 'rows': rows,
        'total_rows': len(df), 'total_cols': len(columns),
    })


@login_required
def change_password(request):
    error = None
    success = None
    if request.method == 'POST':
        current = request.POST.get('current_password', '')
        new_pw  = request.POST.get('new_password', '')
        confirm = request.POST.get('confirm_password', '')
        user    = request.user
        if not user.check_password(current):
            error = 'Current password is incorrect.'
        elif len(new_pw) < 6:
            error = 'New password must be at least 6 characters.'
        elif new_pw != confirm:
            error = 'New passwords do not match.'
        else:
            user.set_password(new_pw)
            user.save()
            from django.contrib.auth import update_session_auth_hash
            update_session_auth_hash(request, user)
            success = 'Password changed successfully.'
    return render(request, 'change_password.html', {'error': error, 'success': success})


@login_required
def upload_profile_photo(request):
    if request.method != 'POST':
        return redirect('upload_query')

    try:
        profile = Profile.objects.get(user=request.user)
    except Profile.DoesNotExist:
        profile = Profile.objects.create(user=request.user)

    form = ProfilePhotoForm(request.POST, request.FILES, instance=profile)
    if form.is_valid():
        form.save()
    return redirect('upload_query')


@login_required
def delete_profile_photo(request):
    try:
        profile = Profile.objects.get(user=request.user)
        if profile.photo:
            profile.photo.delete()
            profile.save()
    except Profile.DoesNotExist:
        pass
    return redirect('upload_query')



    record = get_object_or_404(UploadedFile, pk=pk)
    if not os.path.exists(record.file.path):
        return HttpResponse('File not found.', status=404)
    df = read_file(record.file.path)
    if not hasattr(df, 'head'):
        return HttpResponse('Only CSV/XLSX files can be manually edited.', status=400)

    if request.method == 'POST':
        try:
            payload = json.loads(request.body)
            row_idx = int(payload['row'])
            col_name = payload['col']
            new_value = payload['value']
            if col_name not in df.columns:
                return JsonResponse({'error': 'Invalid column'}, status=400)
            # cast to original dtype if numeric
            if pd.api.types.is_numeric_dtype(df[col_name]):
                try:
                    new_value = float(new_value)
                except ValueError:
                    return JsonResponse({'error': 'Expected a numeric value for this column'}, status=400)
            df.at[row_idx, col_name] = new_value
            ext = os.path.splitext(record.file.path)[1].lower()
            if ext == '.csv':
                df.to_csv(record.file.path, index=False)
            else:
                df.to_excel(record.file.path, index=False)
            return JsonResponse({'success': True, 'value': str(new_value)})
        except Exception as e:
            return JsonResponse({'error': str(e)}, status=500)

    columns = list(df.columns)
    rows = df.values.tolist()
    return render(request, 'edit.html', {
        'record': record,
        'columns': columns,
        'rows': rows,
        'total_rows': len(df),
        'total_cols': len(columns),
    })
