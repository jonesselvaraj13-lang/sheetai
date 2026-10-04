import re
import os
import pandas as pd
import openai
from html import escape
from docx import Document
import PyPDF2


def _dedup_columns(df):
    """Ensure all column names are unique strings."""
    if not df.columns.duplicated().any():
        return df
    seen, new_cols = {}, []
    for c in df.columns:
        c = str(c)
        if c in seen:
            seen[c] += 1
            new_cols.append(f'{c}_{seen[c]}')
        else:
            seen[c] = 0
            new_cols.append(c)
    df = df.copy()
    df.columns = new_cols
    return df


def _coerce_numeric_like_columns(df):
    """Convert object columns that are clearly numeric into numbers for analysis."""
    df = df.copy()
    for col in df.columns:
        if pd.api.types.is_numeric_dtype(df[col]):
            continue
        series = df[col]
        non_null = series.dropna()
        if non_null.empty:
            continue
        cleaned = (
            non_null.astype(str)
            .str.strip()
            .str.replace(',', '', regex=False)
            .str.replace(r'^[^\d\-+.]+', '', regex=True)
            .str.replace(r'[^\d.%-]+$', '', regex=True)
        )
        is_percent = cleaned.str.endswith('%')
        cleaned = cleaned.str.replace('%', '', regex=False)
        converted = pd.to_numeric(cleaned, errors='coerce')
        usable_ratio = converted.notna().mean()
        if usable_ratio >= 0.85:
            full_cleaned = (
                series.astype(str)
                .str.strip()
                .str.replace(',', '', regex=False)
                .str.replace(r'^[^\d\-+.]+', '', regex=True)
                .str.replace(r'[^\d.%-]+$', '', regex=True)
            )
            full_is_percent = full_cleaned.str.endswith('%')
            full_cleaned = full_cleaned.str.replace('%', '', regex=False)
            full_converted = pd.to_numeric(full_cleaned, errors='coerce')
            if is_percent.any() or full_is_percent.any():
                full_converted = full_converted / 100
            df[col] = full_converted
    return df


def analyze_data_quality(df):
    df = _dedup_columns(df)
    total_rows = len(df)
    if total_rows == 0:
        return None
    duplicate_count = int(df.duplicated().sum())
    columns, total_noise_cells = [], 0
    total_cells = total_rows * len(df.columns)
    for col in df.columns:
        try:
            s = df[col]
            missing = int(s.isnull().sum())
            outlier_count = 0
            if pd.api.types.is_numeric_dtype(s):
                q1, q3 = s.quantile(0.25), s.quantile(0.75)
                iqr = q3 - q1
                if iqr > 0:
                    outlier_count = int(s[(s < q1 - 1.5*iqr) | (s > q3 + 1.5*iqr)].count())
            clean_pct = round(((total_rows - missing) / total_rows) * 100, 1)
            total_noise_cells += missing + outlier_count
            columns.append({'name': col, 'clean_pct': clean_pct, 'noise_pct': round(100 - clean_pct, 1),
                            'missing': missing, 'outliers': outlier_count, 'dtype': str(s.dtype)})
        except Exception:
            continue
    overall_noise_pct = round((total_noise_cells / total_cells) * 100, 1) if total_cells > 0 else 0
    return {'total_rows': total_rows, 'total_cols': len(df.columns), 'duplicate_rows': duplicate_count,
            'overall_clean_pct': round(100 - overall_noise_pct, 1), 'overall_noise_pct': overall_noise_pct,
            'columns': columns}


def clean_noise_data(df):
    df = df.drop_duplicates()
    df = df.dropna(how='all')
    for col in df.columns:
        if pd.api.types.is_numeric_dtype(df[col]):
            median = df[col].median()
            df[col] = df[col].fillna(median)
            q1 = df[col].quantile(0.25)
            q3 = df[col].quantile(0.75)
            iqr = q3 - q1
            if iqr > 0:
                lower = q1 - 1.5 * iqr
                upper = q3 + 1.5 * iqr
                df[col] = df[col].clip(lower=lower, upper=upper)
        else:
            if df[col].isnull().any():
                mode_vals = df[col].mode()
                fill_val = mode_vals[0] if len(mode_vals) > 0 else 'Unknown'
                df[col] = df[col].fillna(fill_val)
    return df


def text_to_df(text):
    sentences = [s.strip() for s in re.split(r'(?<=[.!?])\s+|\n+', text) if s.strip()]
    df = pd.DataFrame({'sentence': sentences})
    df['word_count'] = df['sentence'].apply(lambda x: len(x.split()))
    df['char_count'] = df['sentence'].apply(len)
    return df


def clean_text_noise(df):
    df = df[df['sentence'].str.strip() != '']
    df = df.drop_duplicates(subset=['sentence'])
    df = df[df['sentence'].str.contains(r'[a-zA-Z]', regex=True)]
    df['sentence'] = df['sentence'].str.strip()
    df = df.reset_index(drop=True)
    return df


def format_analysis_result(analysis_text, data_preview_html, filename='', prompt='', business_insights_html='', insights_title='Insights', important_details_html=''):
    q = prompt if prompt else 'Data Preview'
    preview_section = (
        '<div class="analysis-section">'
        '<div class="section-header">'
        '<h3 class="section-title">Data Preview</h3>'
        '</div>'
        '<div class="section-content data-preview">' + str(data_preview_html) + '</div>'
        '</div>'
    ) if data_preview_html else ''
    business_section = (
        '<div class="analysis-section">'
        '<div class="section-header">'
        '<h3 class="section-title">' + escape(str(insights_title)) + '</h3>'
        '<span class="section-badge">Insights</span>'
        '</div>'
        '<div class="section-content">' + str(business_insights_html) + '</div>'
        '</div>'
    ) if business_insights_html else ''
    details_section = (
        '<div class="analysis-section">'
        '<div class="section-header">'
        '<h3 class="section-title">Important Details</h3>'
        '<span class="section-badge">Details</span>'
        '</div>'
        '<div class="section-content">' + str(important_details_html) + '</div>'
        '</div>'
    ) if important_details_html else ''
    return (
        '<div class="analysis-container">'
        '<div class="analysis-metadata">'
        '<div class="meta-item"><span class="meta-label">File</span><span class="meta-value">' + str(filename) + '</span></div>'
        '<div class="meta-item"><span class="meta-label">Query</span><span class="meta-value">' + str(q) + '</span></div>'
        '</div>'
        '<div class="analysis-section">'
        '<div class="section-header">'
        '<h3 class="section-title">Result</h3>'
        '<span class="section-badge">Answer</span>'
        '</div>'
        '<div class="section-content">' + str(analysis_text) + '</div>'
        '</div>'
        + business_section
        + details_section
        + preview_section +
        '</div>'
    )


def render_text_answer_html(answer):
    """Render plain document-answer text as a clean result panel."""
    if not answer:
        return '<div class="document-answer empty">No answer generated.</div>'

    lines = [str(line).strip() for line in str(answer).splitlines()]
    lines = [line for line in lines if line and set(line) != {'-'}]
    if not lines:
        return '<div class="document-answer empty">No answer generated.</div>'

    title = lines[0]
    meta = []
    sections = []
    current = None

    for line in lines[1:]:
        if line.startswith('Topic:') or line.startswith('Total words:') or line.startswith('Query:'):
            label, value = line.split(':', 1)
            meta.append((label.strip(), value.strip()))
            continue
        if line.endswith(':') and len(line) <= 80:
            current = {'title': line[:-1].strip(), 'items': []}
            sections.append(current)
            continue
        if current is None:
            current = {'title': 'Answer', 'items': []}
            sections.append(current)
        current['items'].append(line[2:].strip() if line.startswith('- ') else line)

    html = ['<div class="document-answer">']
    html.append('<div class="doc-answer-hero">')
    html.append('<div class="doc-answer-kicker">Answer</div>')
    html.append(f'<h4>{escape(title)}</h4>')
    if meta:
        html.append('<div class="doc-answer-meta">')
        for label, value in meta:
            html.append(
                '<span class="doc-meta-chip">'
                f'<strong>{escape(label)}</strong>'
                f'<span>{escape(value)}</span>'
                '</span>'
            )
        html.append('</div>')
    html.append('</div>')

    for section in sections:
        if not section['items']:
            continue
        html.append('<div class="doc-answer-section">')
        html.append(f'<h5>{escape(section["title"])}</h5>')
        html.append('<ul>')
        for item in section['items']:
            html.append(f'<li>{escape(item)}</li>')
        html.append('</ul>')
        html.append('</div>')

    html.append('</div>')
    return ''.join(html)


def ai_query(df, prompt):
    data_sample = df.head(20).to_string()
    api_key = os.environ.get('OPENAI_API_KEY', '').strip()
    client = openai.OpenAI(api_key=api_key)
    response = client.chat.completions.create(
        model='gpt-4o-mini',
        messages=[
            {'role': 'system', 'content': 'You are a data analyst. Answer ONLY using given dataset.'},
            {'role': 'user', 'content': 'Dataset:\n' + data_sample + '\n\nQuestion:\n' + prompt}
        ]
    )
    return response.choices[0].message.content


def compare_datasets(df1, df2, name1='Source 1', name2='Source 2', prompt=''):
    rows = []
    rows.append({'Metric': 'Rows', name1: len(df1), name2: len(df2), 'Difference': len(df1) - len(df2)})
    rows.append({'Metric': 'Columns', name1: len(df1.columns), name2: len(df2.columns), 'Difference': len(df1.columns) - len(df2.columns)})
    miss1 = int(df1.isnull().sum().sum())
    miss2 = int(df2.isnull().sum().sum())
    rows.append({'Metric': 'Missing Values', name1: miss1, name2: miss2, 'Difference': miss1 - miss2})
    dup1 = int(df1.duplicated().sum())
    dup2 = int(df2.duplicated().sum())
    rows.append({'Metric': 'Duplicate Rows', name1: dup1, name2: dup2, 'Difference': dup1 - dup2})
    num1 = df1.select_dtypes(include='number')
    num2 = df2.select_dtypes(include='number')
    rows.append({'Metric': 'Numeric Columns', name1: len(num1.columns), name2: len(num2.columns), 'Difference': len(num1.columns) - len(num2.columns)})
    if not num1.empty:
        rows.append({'Metric': 'Numeric Mean (avg)', name1: round(num1.mean().mean(), 2), name2: round(num2.mean().mean(), 2) if not num2.empty else 'N/A', 'Difference': ''})
        rows.append({'Metric': 'Numeric Std (avg)', name1: round(num1.std().mean(), 2), name2: round(num2.std().mean(), 2) if not num2.empty else 'N/A', 'Difference': ''})
    common_cols = list(set(df1.columns) & set(df2.columns))
    only_in_1 = list(set(df1.columns) - set(df2.columns))
    only_in_2 = list(set(df2.columns) - set(df1.columns))
    rows.append({'Metric': 'Common Columns', name1: len(common_cols), name2: len(common_cols), 'Difference': 0})
    rows.append({'Metric': 'Unique to Source', name1: ', '.join(only_in_1) or '—', name2: ', '.join(only_in_2) or '—', 'Difference': ''})
    summary_df = pd.DataFrame(rows)

    col_rows = []
    for col in common_cols[:15]:
        c1 = df1[col]
        c2 = df2[col]
        miss_c1 = int(c1.isnull().sum())
        miss_c2 = int(c2.isnull().sum())
        if pd.api.types.is_numeric_dtype(c1) and pd.api.types.is_numeric_dtype(c2):
            col_rows.append({'Column': col, 'Type': str(c1.dtype), name1 + ' Mean': round(c1.mean(), 2), name2 + ' Mean': round(c2.mean(), 2), name1 + ' Missing': miss_c1, name2 + ' Missing': miss_c2})
        else:
            col_rows.append({'Column': col, 'Type': str(c1.dtype), name1 + ' Mean': '—', name2 + ' Mean': '—', name1 + ' Missing': miss_c1, name2 + ' Missing': miss_c2})
    col_df = pd.DataFrame(col_rows) if col_rows else None

    # Prompt-based comparison
    prompt_result = None
    if prompt and prompt.strip():
        prompt_result = compare_with_prompt(df1, df2, name1, name2, prompt)

    return summary_df, col_df, common_cols, only_in_1, only_in_2, prompt_result


def compare_with_prompt(df1, df2, name1, name2, prompt):
    prompt_lower = prompt.lower().strip()

    # Try OpenAI first
    try:
        api_key = os.environ.get('OPENAI_API_KEY')
        if api_key and api_key != 'YOUR_API_KEY':
            info = f"""You have TWO datasets to compare.

{name1} — {len(df1)} rows, {len(df1.columns)} cols
Columns: {', '.join(df1.columns.tolist())}
Sample:
{df1.head(5).to_string()}

{name2} — {len(df2)} rows, {len(df2.columns)} cols
Columns: {', '.join(df2.columns.tolist())}
Sample:
{df2.head(5).to_string()}
"""
            client = openai.OpenAI(api_key=api_key)
            response = client.chat.completions.create(
                model='gpt-4o-mini',
                messages=[
                    {'role': 'system', 'content': 'You are a data analyst. Compare the two datasets and answer the user query clearly with insights, differences and similarities.'},
                    {'role': 'user', 'content': info + '\nUser Query: ' + prompt}
                ],
                temperature=0.2,
                max_tokens=800
            )
            return response.choices[0].message.content.strip()
    except Exception:
        pass

    # Rule-based fallback
    results = []

    if 'missing' in prompt_lower or 'null' in prompt_lower:
        r1 = df1.isnull().sum()
        r2 = df2.isnull().sum()
        results.append(f"{name1} missing values:\n{r1[r1>0].to_string() or 'None'}")
        results.append(f"{name2} missing values:\n{r2[r2>0].to_string() or 'None'}")

    elif 'duplicate' in prompt_lower:
        results.append(f"{name1} duplicates: {df1.duplicated().sum()}")
        results.append(f"{name2} duplicates: {df2.duplicated().sum()}")

    elif 'describe' in prompt_lower or 'summary' in prompt_lower or 'statistic' in prompt_lower:
        results.append(f"{name1} statistics:\n{df1.describe().to_string()}")
        results.append(f"{name2} statistics:\n{df2.describe().to_string()}")

    elif 'column' in prompt_lower:
        common = list(set(df1.columns) & set(df2.columns))
        only1 = list(set(df1.columns) - set(df2.columns))
        only2 = list(set(df2.columns) - set(df1.columns))
        results.append(f"Common columns ({len(common)}): {', '.join(common) or 'None'}")
        results.append(f"Only in {name1}: {', '.join(only1) or 'None'}")
        results.append(f"Only in {name2}: {', '.join(only2) or 'None'}")

    elif 'row' in prompt_lower or 'size' in prompt_lower or 'shape' in prompt_lower:
        results.append(f"{name1}: {len(df1)} rows × {len(df1.columns)} columns")
        results.append(f"{name2}: {len(df2)} rows × {len(df2.columns)} columns")
        results.append(f"Difference: {abs(len(df1) - len(df2))} rows")

    else:
        # Generic: apply prompt to each df and show side by side
        r1 = process_prompt(df1, prompt)
        r2 = process_prompt(df2, prompt)
        results.append(f"{name1} result:\n{r1.to_string() if hasattr(r1, 'to_string') else str(r1)}")
        results.append(f"{name2} result:\n{r2.to_string() if hasattr(r2, 'to_string') else str(r2)}")

    return '\n\n'.join(results)


def parse_seating_file(file_path):
    """Parse exam seating arrangement Excel files into structured student records."""
    try:
        raw = pd.read_excel(file_path, header=None)
    except Exception as e:
        return None

    records = []
    for row_idx in range(len(raw)):
        row = raw.iloc[row_idx]
        row_vals = [str(v).strip() if pd.notna(v) else '' for v in row]

        # Detect class label rows containing YEAR keyword
        class_labels = {}
        for col_idx, val in enumerate(row_vals):
            if 'YEAR' in val.upper() and len(val) > 3:
                class_labels[col_idx] = val.replace('\n', ' ').strip()
        if not class_labels:
            continue

        data_row = row_idx + 1
        while data_row < len(raw):
            data = raw.iloc[data_row]
            data_vals = [str(v).strip() if pd.notna(v) else '' for v in data]

            if any('YEAR' in str(v).upper() for v in data_vals if pd.notna(v) and len(str(v)) > 3):
                break
            if any(kw in str(data_vals[0]).upper() for kw in ['TOTAL', 'ABSENT', 'HALL', 'DATE', 'CENTRE', 'DHANA', 'PROFORMA', 'SUPERINTENDENT']):
                break

            for pair_start in range(0, len(data_vals) - 1, 2):
                seat = data_vals[pair_start]
                reg = data_vals[pair_start + 1] if pair_start + 1 < len(data_vals) else ''
                if not re.match(r'^[A-Z]\d+$', seat):
                    continue
                if not re.match(r'^\d{8,}$', reg):
                    continue

                class_name = ''
                for cl_col in sorted(class_labels.keys(), reverse=True):
                    if cl_col <= pair_start:
                        class_name = class_labels[cl_col]
                        break
                if not class_name and class_labels:
                    class_name = list(class_labels.values())[0]

                year_match = re.search(r'(I{1,3}|IV|V)\s*YEAR', class_name, re.IGNORECASE)
                year = year_match.group(0).strip() if year_match else ''
                dept_match = re.search(r'(AIDS|MECH|CSE|ECE|EEE|IT|CIVIL|MBA|MCA|AIML|CSD|BT|BME)', class_name, re.IGNORECASE)
                dept = dept_match.group(0).upper() if dept_match else class_name

                records.append({
                    'Seat': seat,
                    'Register Number': reg,
                    'Class': class_name,
                    'Department': dept,
                    'Year': year,
                })
            data_row += 1

    if not records:
        return None
    return pd.DataFrame(records).drop_duplicates(subset=['Register Number']).reset_index(drop=True)


def smart_read_excel(file_path):
    """Read Excel files — all sheets combined, handles messy headers."""
    try:
        xl = pd.ExcelFile(file_path)
        sheet_names = xl.sheet_names
    except Exception as e:
        return 'Error reading file: ' + str(e)

    def _read_sheet(sheet):
        try:
            raw = pd.read_excel(file_path, sheet_name=sheet, header=None)
        except Exception:
            return None
        best_row, best_score = 0, 0
        for i in range(min(20, len(raw))):
            score = sum(1 for v in raw.iloc[i] if isinstance(v, str) and len(str(v).strip()) > 1)
            if score > best_score:
                best_score, best_row = score, i
        try:
            df = pd.read_excel(file_path, sheet_name=sheet, header=best_row)
        except Exception:
            return None
        new_cols, last_valid = [], ''
        for i, col in enumerate(df.columns):
            col_str = str(col).strip().replace('\n', ' ').replace('\r', ' ')
            col_str = re.sub(r'\s+', ' ', col_str).strip()
            if col_str.startswith('Unnamed') or col_str == 'nan' or col_str == '':
                new_cols.append(last_valid + f'_{i}' if last_valid else f'col_{i}')
            else:
                col_clean = re.sub(r'\.\d+$', '', col_str).strip()
                last_valid = col_clean
                new_cols.append(col_clean)
        seen = {}
        deduped = []
        for c in new_cols:
            if c in seen:
                seen[c] += 1
                deduped.append(f'{c}_{seen[c]}')
            else:
                seen[c] = 0
                deduped.append(c)
        df.columns = deduped
        df = df.dropna(how='all').dropna(axis=1, how='all').reset_index(drop=True)
        if len(sheet_names) > 1:
            df.insert(0, '_sheet', str(sheet))
        return df

    frames = []
    for sheet in sheet_names:
        df = _read_sheet(sheet)
        if df is not None and not df.empty:
            frames.append(df)

    if not frames:
        return 'Error reading Excel: no data found in any sheet.'

    try:
        combined = pd.concat(frames, ignore_index=True, sort=False)
        combined = _dedup_columns(combined)
        for col in combined.columns:
            if col == '_sheet':
                continue
            if combined[col].dtype == object:
                combined[col] = combined[col].fillna('')
            else:
                combined[col] = combined[col].fillna(0)
        return _coerce_numeric_like_columns(combined)
    except Exception:
        return _coerce_numeric_like_columns(frames[0].drop(columns=['_sheet'], errors='ignore'))


def read_url_text(url):
    """Extract plain text content from any webpage URL for prompt-based analysis."""
    import urllib.request
    import urllib.parse
    import json
    from html.parser import HTMLParser

    class TextExtractor(HTMLParser):
        def __init__(self):
            super().__init__()
            self.text_parts = []
            self.skip_tags = {'script', 'style', 'head', 'meta', 'link', 'noscript', 'sup'}
            self.skip_classes = {
                'navbox', 'mw-editsection', 'reference', 'reflist', 'toc',
                'sidebar', 'metadata', 'noprint', 'hatnote'
            }
            self.current_skip = 0
            self.skip_stack = []
        def handle_starttag(self, tag, attrs):
            if tag in self.skip_tags:
                self.current_skip += 1
                self.skip_stack.append(tag)
                return
            attr_map = {k: v for k, v in attrs}
            class_text = attr_map.get('class', '')
            if any(skip in class_text for skip in self.skip_classes):
                self.current_skip += 1
                self.skip_stack.append(tag)
        def handle_endtag(self, tag):
            if self.skip_stack and tag == self.skip_stack[-1]:
                self.skip_stack.pop()
                self.current_skip = max(0, self.current_skip - 1)
        def handle_data(self, data):
            if self.current_skip == 0:
                text = data.strip()
                if text:
                    self.text_parts.append(text)
        def get_text(self):
            return '\n'.join(self.text_parts)

    headers = {
        'User-Agent': 'SheetAI/1.0 (educational data analysis tool)',
        'Accept': 'text/html,application/xhtml+xml,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.9',
    }

    def _fetch_text(fetch_url, accept='text/html,application/xhtml+xml,*/*;q=0.8'):
        req_headers = headers.copy()
        req_headers['Accept'] = accept
        req = urllib.request.Request(fetch_url, headers=req_headers)
        with urllib.request.urlopen(req, timeout=30) as response:
            raw = response.read()
            content_type = response.headers.get('Content-Type', '')
        content_type_lower = content_type.lower()
        binary_types = [
            'application/pdf',
            'application/msword',
            'application/vnd.openxmlformats-officedocument',
            'application/vnd.ms-excel',
            'application/octet-stream',
            'image/',
        ]
        if any(t in content_type_lower for t in binary_types):
            return ''
        charset = 'utf-8'
        if 'charset=' in content_type:
            charset = content_type.split('charset=', 1)[1].split(';', 1)[0].strip()
        return raw.decode(charset or 'utf-8', errors='ignore')

    def _wikipedia_title(parsed_url):
        path = parsed_url.path.replace('/wiki/', '/wiki/')
        if '/wiki/' not in path:
            return ''
        title = path.split('/wiki/', 1)[1]
        title = title.split('#', 1)[0].split('?', 1)[0]
        return urllib.parse.unquote(title).replace('_', ' ').strip()

    def _wikipedia_host(parsed_url):
        host = parsed_url.netloc.replace('m.wikipedia.org', 'wikipedia.org')
        if host.startswith('mobile.'):
            host = host.replace('mobile.', '')
        return host

    def _wikipedia_search_title(parsed_url):
        query = urllib.parse.parse_qs(parsed_url.query)
        search_term = (query.get('search') or query.get('title') or [''])[0]
        if not search_term or search_term.lower() == 'special:search':
            return ''
        scheme = parsed_url.scheme or 'https'
        host = _wikipedia_host(parsed_url)
        api_url = (
            f'{scheme}://{host}/w/api.php?action=query&list=search&format=json'
            f'&srlimit=1&srsearch={urllib.parse.quote(search_term)}'
        )
        try:
            data = _fetch_text(api_url, accept='application/json')
            payload = json.loads(data)
            results = payload.get('query', {}).get('search', [])
            if results:
                return results[0].get('title', '')
        except Exception:
            return ''
        return ''

    def _read_wikipedia_by_title(parsed_url, title):
        if not title:
            return ''
        scheme = parsed_url.scheme or 'https'
        host = _wikipedia_host(parsed_url)
        encoded_title = urllib.parse.quote(title.replace(' ', '_'), safe='')

        api_candidates = [
            f'{scheme}://{host}/w/api.php?action=query&prop=extracts&explaintext=1&exsectionformat=plain&redirects=1&format=json&titles={urllib.parse.quote(title)}',
            f'{scheme}://{host}/api/rest_v1/page/summary/{encoded_title}',
        ]
        for api_url in api_candidates:
            try:
                data = _fetch_text(api_url, accept='application/json')
                payload = json.loads(data)
                if 'extract' in payload and payload.get('extract'):
                    return clean_extracted_text(payload.get('extract', ''))
                pages = payload.get('query', {}).get('pages', {})
                extracts = [page.get('extract', '') for page in pages.values() if page.get('extract')]
                if extracts:
                    return clean_extracted_text('\n'.join(extracts))
            except Exception:
                continue
        return ''

    def _read_wikipedia(parsed_url):
        title = _wikipedia_title(parsed_url) or _wikipedia_search_title(parsed_url)
        return _read_wikipedia_by_title(parsed_url, title)

    parsed = urllib.parse.urlparse(url)
    if 'wikipedia.org' in parsed.netloc and ('/wiki/' in parsed.path or parsed.path.endswith('/w/index.php')):
        wiki_text = _read_wikipedia(parsed)
        if wiki_text:
            return wiki_text

    try:
        html = _fetch_text(url)
        parser = TextExtractor()
        parser.feed(html)
        text = parser.get_text()
        # Clean up excessive blank lines
        lines = [l.strip() for l in text.splitlines() if l.strip()]
        text = '\n'.join(lines)
        if text:
            return text
    except Exception as e:
        pass

    # Wikipedia fallback: use its API when direct HTML extraction is blocked or too noisy.
    try:
        if 'wikipedia.org' in parsed.netloc and ('/wiki/' in parsed.path or parsed.path.endswith('/w/index.php')):
            return _read_wikipedia(parsed)
    except Exception:
        pass

    return ''


def read_url(url):
    import urllib.request
    import urllib.parse
    import io
    import json

    def _google_sheet_export_url(raw_url):
        parsed = urllib.parse.urlparse(raw_url)
        if 'docs.google.com' not in parsed.netloc or '/spreadsheets/' not in parsed.path:
            return raw_url
        match = re.search(r'/spreadsheets/d/([a-zA-Z0-9_-]+)', parsed.path)
        if not match:
            return raw_url
        query = urllib.parse.parse_qs(parsed.query)
        gid = (query.get('gid') or ['0'])[0]
        return f'https://docs.google.com/spreadsheets/d/{match.group(1)}/export?format=csv&gid={gid}'

    url = _google_sheet_export_url(url)
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    }
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=30) as response:
            data = response.read()
            content_type = response.headers.get('Content-Type', '')
        url_lower = url.lower().split('?')[0]
        content_type_lower = content_type.lower()
        if url_lower.endswith('.pdf') or 'application/pdf' in content_type_lower:
            try:
                reader = PyPDF2.PdfReader(io.BytesIO(data), strict=False)
                text = ''
                for page in reader.pages:
                    text += (page.extract_text() or '') + '\n'
                return clean_extracted_text(text) or 'No readable text found in this PDF link.'
            except Exception as e:
                return 'Error reading PDF URL: ' + str(e)
        if url_lower.endswith('.docx') or 'application/vnd.openxmlformats-officedocument.wordprocessingml.document' in content_type_lower:
            try:
                doc = Document(io.BytesIO(data))
                text = '\n'.join([p.text for p in doc.paragraphs])
                for table in doc.tables:
                    for row in table.rows:
                        text += '\n' + ' | '.join(cell.text.strip() for cell in row.cells if cell.text.strip())
                return clean_extracted_text(text)
            except Exception as e:
                return 'Error reading DOCX URL: ' + str(e)
        if url_lower.endswith('.json') or 'application/json' in content_type_lower:
            try:
                payload = json.loads(data.decode('utf-8', errors='ignore'))
                if isinstance(payload, list):
                    return _dedup_columns(pd.json_normalize(payload).head(5000))
                if isinstance(payload, dict):
                    for value in payload.values():
                        if isinstance(value, list):
                            return _dedup_columns(pd.json_normalize(value).head(5000))
                    return clean_extracted_text(json.dumps(payload, indent=2))
            except Exception as e:
                return 'Error reading JSON URL: ' + str(e)
        if content_type_lower.startswith('image/') or url_lower.endswith(('.png', '.jpg', '.jpeg', '.bmp', '.tif', '.tiff', '.webp')):
            return 'Image analysis is not supported. Please use CSV, Excel, PDF, DOCX, TXT, JSON, or webpage URLs.'
        if url_lower.endswith(('.txt', '.md', '.log')) or content_type_lower.startswith('text/plain'):
            charset = 'utf-8'
            if 'charset=' in content_type:
                charset = content_type.split('charset=', 1)[1].split(';', 1)[0].strip()
            return clean_extracted_text(data.decode(charset or 'utf-8', errors='ignore'))
        if url_lower.endswith(('.xls', '.xlsx')):
            return smart_read_excel_bytes(data)
        if url_lower.endswith('.csv') or any(x in content_type_lower for x in ['text/csv', 'application/csv']):
            return _dedup_columns(pd.read_csv(io.BytesIO(data), nrows=5000))
        if any(x in content_type_lower for x in [
            'application/vnd.ms-excel',
            'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        ]):
            return smart_read_excel_bytes(data)
        if 'text/html' in content_type_lower:
            try:
                tables = pd.read_html(io.BytesIO(data))
                if tables:
                    return _dedup_columns(max(tables, key=len).dropna(how='all').dropna(axis=1, how='all').reset_index(drop=True))
            except Exception:
                pass
            return None
        try:
            return _dedup_columns(pd.read_csv(io.BytesIO(data), nrows=5000))
        except Exception:
            pass
        try:
            tables = pd.read_html(io.BytesIO(data))
            if tables:
                return _dedup_columns(max(tables, key=len).dropna(how='all').dropna(axis=1, how='all').reset_index(drop=True))
        except Exception:
            pass
        return None
    except Exception as e:
        return 'Error reading URL: ' + str(e)


def smart_read_excel_bytes(data):
    import io
    try:
        xl = pd.ExcelFile(io.BytesIO(data))
        sheet_names = xl.sheet_names
    except Exception as e:
        return 'Error reading Excel from URL: ' + str(e)

    def _read_sheet(sheet):
        try:
            raw = pd.read_excel(io.BytesIO(data), sheet_name=sheet, header=None, nrows=5000)
            best_row, best_score = 0, 0
            for i in range(min(20, len(raw))):
                score = sum(1 for v in raw.iloc[i] if isinstance(v, str) and len(str(v).strip()) > 1)
                if score > best_score:
                    best_score, best_row = score, i
            df = pd.read_excel(io.BytesIO(data), sheet_name=sheet, header=best_row, nrows=5000)
            new_cols, last_valid = [], ''
            for i, col in enumerate(df.columns):
                col_str = str(col).strip().replace('\n', ' ').replace('\r', ' ')
                col_str = re.sub(r'\s+', ' ', col_str).strip()
                if col_str.startswith('Unnamed') or col_str == 'nan' or col_str == '':
                    new_cols.append(last_valid + f'_{i}' if last_valid else f'col_{i}')
                else:
                    col_clean = re.sub(r'\.\d+$', '', col_str).strip()
                    last_valid = col_clean
                    new_cols.append(col_clean)
            seen = {}
            deduped = []
            for c in new_cols:
                if c in seen:
                    seen[c] += 1
                    deduped.append(f'{c}_{seen[c]}')
                else:
                    seen[c] = 0
                    deduped.append(c)
            df.columns = deduped
            df = df.dropna(how='all').dropna(axis=1, how='all').reset_index(drop=True)
            if len(sheet_names) > 1:
                df.insert(0, '_sheet', str(sheet))
            return df
        except Exception:
            return None

    if len(sheet_names) == 1:
        result = _read_sheet(sheet_names[0])
        return result if result is not None else 'Error reading Excel sheet.'

    frames = [_read_sheet(s) for s in sheet_names]
    frames = [f for f in frames if f is not None and not f.empty]
    if not frames:
        return 'Error reading Excel: no data found.'
    try:
        return pd.concat(frames, ignore_index=True, sort=False)
    except Exception:
        return frames[0]


def _read_pdf_text(file_path):
    """Extract text from PDF. Handles corrupted PDFs and CSV files saved with .pdf extension."""
    try:
        with open(file_path, 'rb') as f:
            header = f.read(512)
        if not header.startswith(b'%PDF'):
            try:
                df = pd.read_csv(file_path)
                if 'sentence' in df.columns:
                    return '\n'.join(df['sentence'].dropna().astype(str).tolist())
                return df.to_string(index=False)
            except Exception:
                return header.decode('utf-8', errors='ignore')
    except Exception:
        pass
    try:
        with open(file_path, 'rb') as f:
            reader = PyPDF2.PdfReader(f, strict=False)
            text = ''
            for page in reader.pages:
                text += (page.extract_text() or '') + '\n'
        return clean_extracted_text(text)
    except Exception as e:
        return 'Error reading PDF: ' + str(e)


def clean_extracted_text(text):
    """Clean noisy PDF/web extraction into readable lines."""
    if not text:
        return ''
    text = text.replace('\r', '\n')
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r'(?<=\w)-\s*\n\s*(?=\w)', '', text)
    text = re.sub(r'(?<=\w)\s*\n\s*(?=[a-z,.;:)])', ' ', text)
    text = re.sub(r'\n{3,}', '\n\n', text)

    cleaned_lines = []
    for raw_line in text.splitlines():
        line = re.sub(r'\s+', ' ', raw_line).strip()
        if not line:
            continue
        tokens = line.split()
        single_char_tokens = sum(1 for token in tokens if len(token.strip(':,.;/-')) == 1)
        if len(tokens) >= 10 and single_char_tokens / len(tokens) > 0.55:
            continue
        line = re.sub(r'\bB\+\s+T\s*ree\b', 'B+ Tree', line, flags=re.IGNORECASE)
        line = re.sub(r'\bB-\s*T\s*ree\b', 'B-Tree', line, flags=re.IGNORECASE)
        line = re.sub(r'\bK\s*ey\b', 'Key', line, flags=re.IGNORECASE)
        line = re.sub(r'\bDif\s*ferences\b', 'Differences', line, flags=re.IGNORECASE)
        line = re.sub(r'\bcons\s+iderations\b', 'considerations', line, flags=re.IGNORECASE)
        line = re.sub(r'\bwo\s+rkloads\b', 'workloads', line, flags=re.IGNORECASE)
        line = re.sub(r'\ba\s+nd\b', 'and', line, flags=re.IGNORECASE)
        cleaned_lines.append(line)
    return '\n'.join(cleaned_lines)


def _extract_document_headings(lines):
    headings = []
    for line in lines:
        word_count = len(line.split())
        if 2 <= word_count <= 10 and len(line) <= 90:
            has_heading_case = line[:1].isupper() and not line.endswith(('.', ',', ';'))
            has_keyword = any(k in line.lower() for k in [
                'what is', 'advantages', 'usage', 'examples', 'structure',
                'insertion', 'deletion', 'differences', 'benefits'
            ])
            if has_heading_case or has_keyword:
                headings.append(line)
    seen = set()
    unique = []
    for heading in headings:
        key = heading.lower()
        if key not in seen:
            seen.add(key)
            unique.append(heading)
    return unique[:10]


def _sentence_summary(text, limit=7):
    normalized = re.sub(r'\s+', ' ', text).strip()
    sentences = [s.strip() for s in re.split(r'(?<=[.!?])\s+', normalized) if len(s.strip()) > 35]
    preferred = []
    keywords = [
        'is ', 'are ', 'stores', 'supports', 'enables', 'reduce', 'efficient',
        'range', 'search', 'insert', 'delete', 'performance', 'scalability'
    ]
    for sentence in sentences:
        if any(k in sentence.lower() for k in keywords):
            preferred.append(sentence)
    result = preferred[:limit] or sentences[:limit]
    return result


def _prompt_keywords(prompt):
    stopwords = {
        'show', 'find', 'give', 'list', 'what', 'about', 'details', 'from',
        'this', 'that', 'with', 'have', 'does', 'tell', 'page', 'link', 'url',
        'website', 'analyze', 'analyse', 'the', 'and', 'for', 'are', 'using',
        'prompt', 'file', 'document', 'data', 'please', 'explain', 'describe',
        'summary', 'summarize', 'overview', 'how', 'why', 'when', 'where',
        'which', 'who', 'is', 'was', 'were', 'can', 'you'
    }
    return [w for w in re.findall(r'[a-zA-Z0-9+_-]{3,}', prompt.lower()) if w not in stopwords]


def _relevant_text_lines(lines, prompt, limit=20):
    keywords = _prompt_keywords(prompt)
    if not keywords:
        return []
    scored = []
    for idx, line in enumerate(lines):
        line_l = line.lower()
        score = sum(1 for kw in keywords if kw in line_l)
        if score:
            scored.append((score, -idx, line))
    scored.sort(reverse=True)
    result = []
    seen = set()
    for _, _, line in scored:
        key = line.lower()
        if key not in seen:
            seen.add(key)
            result.append(line)
        if len(result) >= limit:
            break
    return result


def _text_excerpt_for_prompt(text, prompt, max_chars=12000):
    """Build a focused excerpt so prompt answers use the most relevant content."""
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    relevant = _relevant_text_lines(lines, prompt, limit=24)
    if relevant:
        focused = '\n'.join(relevant)
        intro = '\n'.join(lines[:20])
        excerpt = intro + '\n\nRelevant content:\n' + focused
        return excerpt[:max_chars]
    return text[:max_chars]


def _is_table_prompt(prompt):
    p = (prompt or '').lower()
    table_words = [
        'row', 'rows', 'column', 'columns', 'table', 'filter', 'sort', 'count',
        'sum', 'total', 'average', 'avg', 'mean', 'minimum', 'maximum', 'min',
        'max', 'top', 'bottom', 'group by', 'missing', 'duplicate', 'unique',
        'greater than', 'less than', 'between', 'csv', 'excel', 'sheet',
        'describe', 'statistic', 'distribution', 'percentage', 'percent',
        'revenue', 'sales', 'price', 'marks', 'score', 'amount', 'quantity'
    ]
    return any(word in p for word in table_words) or bool(re.search(r'[<>=]=?|!=', p)) or bool(re.search(r'\bby\b', p))


def _is_general_text_prompt(prompt):
    p = (prompt or '').lower().strip()
    if not p:
        return True
    general_phrases = [
        'summary', 'summarize', 'overview', 'full analysis', 'complete analysis',
        'business insight', 'insights', 'analyze this', 'analyse this',
        'analyze the file', 'analyse the file', 'analyze the url', 'analyse the url',
        'summarize this', 'describe this'
    ]
    if any(phrase in p for phrase in general_phrases):
        return True
    return p in {'analyze', 'analyse', 'describe', 'explain', 'details', 'tell me about this'}


def _direct_text_fact_answer(lines, prompt):
    p = (prompt or '').lower()
    if not lines or not p:
        return None

    asks_count = any(x in p for x in ['how many', 'number of', 'count'])
    trophy_words = any(x in p for x in ['trophy', 'trophies', 'title', 'titles', 'cup', 'cups', 'won', 'wins'])
    if asks_count and trophy_words:
        keywords = _prompt_keywords(prompt)
        candidates = []
        for line in lines:
            line_l = line.lower()
            if keywords and not any(kw in line_l for kw in keywords):
                continue
            if not any(word in line_l for word in ['title', 'titles', 'trophy', 'trophies', 'cup', 'cups', 'champions league']):
                continue
            candidates.append(line)

        champions_candidates = [line for line in candidates if 'champions league' in line.lower()]
        search_lines = champions_candidates or candidates
        for line in search_lines[:12]:
            number_match = re.search(
                r'\b(?:record\s+)?(\d+|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|twenty)\b'
                r'(?=[^.\n]{0,80}(?:champions league|title|titles|trophy|trophies|cup|cups))',
                line,
                re.IGNORECASE
            )
            if number_match:
                value = number_match.group(1)
                return (
                    'Answer\n'
                    '----------------------------------------\n'
                    f'Query: {prompt}\n\n'
                    f'The page says they have won {value} Champions League titles.\n\n'
                    f'Source line: {line}'
                )

    return None


def pdf_text_query(text, prompt):
    """Answer a natural language question about webpage/PDF/DOCX text content."""
    if not text or not text.strip():
        return 'No content could be extracted from this URL.'
    text = clean_extracted_text(text)

    # Try OpenAI
    try:
        api_key = os.environ.get('OPENAI_API_KEY', '').strip()
        if api_key and api_key != 'YOUR_API_KEY':
            client = openai.OpenAI(api_key=api_key)
            excerpt = _text_excerpt_for_prompt(text, prompt)
            response = client.chat.completions.create(
                model='gpt-4o-mini',
                messages=[
                    {'role': 'system', 'content': 'You are a document analyst. Answer the user query based ONLY on the provided content. Keep the answer directly related to the query. If the content does not contain the answer, say that clearly instead of guessing.'},
                    {'role': 'user', 'content': f'Content:\n{excerpt}\n\nQuery: {prompt}'}
                ],
                temperature=0.2,
                max_tokens=1200
            )
            return response.choices[0].message.content.strip()
    except Exception:
        pass

    # Rule-based fallback
    p = prompt.lower()
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    words = text.split()

    direct_fact = _direct_text_fact_answer(lines, prompt)
    if direct_fact:
        return direct_fact

    direct_matches = _relevant_text_lines(lines, prompt, limit=8)
    if direct_matches and not _is_general_text_prompt(prompt):
        answer = [
            'Answer',
            '-' * 40,
            f'Query: {prompt}',
            '',
            'Relevant result:'
        ]
        answer.extend([f'- {line}' for line in direct_matches])
        return '\n'.join(answer)

    if _is_general_text_prompt(prompt):
        title = lines[0] if lines else 'Uploaded Document'
        headings = _extract_document_headings(lines)
        summary = direct_matches or _sentence_summary(text)
        answer = [
            'Clear Document Summary',
            '-' * 40,
            f'Topic: {title}',
            f'Total words: {len(words)} | Total lines: {len(lines)}',
            '',
            'Main Points:'
        ]
        if summary:
            answer.extend([f'- {sentence}' for sentence in summary])
        else:
            answer.append('- The document content was extracted, but it does not contain enough sentence-like text for a detailed summary.')
        if headings:
            answer.extend(['', 'Important Sections:'])
            answer.extend([f'- {heading}' for heading in headings])
        return '\n'.join(answer)

    if any(x in p for x in ['word count', 'how many words', 'length']):
        return f'Word count: {len(words)}\nLine count: {len(lines)}\nCharacter count: {len(text)}'

    if any(x in p for x in ['heading', 'title', 'topic']):
        headings = [l for l in lines if len(l) < 100 and l[0].isupper()][:20]
        return 'Key headings/titles found:\n' + '\n'.join(headings) if headings else '\n'.join(lines[:20])

    # Keyword search from prompt
    keywords = _prompt_keywords(prompt)

    if keywords:
        matched = [l for l in lines if any(k in l.lower() for k in keywords)]
        if matched:
            clean_matches = []
            for line in matched:
                if line not in clean_matches:
                    clean_matches.append(line)
            return f'Found {len(clean_matches)} relevant points for "{prompt}":\n\n' + '\n'.join(f'- {line}' for line in clean_matches[:20])

    # Default: show first 80 lines as content preview
    summary = _sentence_summary(text, limit=5)
    if summary:
        return 'Document Answer\n' + '-' * 40 + '\n' + '\n'.join(f'- {sentence}' for sentence in summary)
    return f'Page content ({len(lines)} lines total):\n\n' + '\n'.join(lines[:30])


def generate_text_insights(text, prompt=''):
    """Generate simple insights for URL/PDF/DOCX text content."""
    text = clean_extracted_text(text)
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    if not lines:
        return ''

    words = text.split()
    relevant = _relevant_text_lines(lines, prompt, limit=3) if prompt else []
    summary = relevant or _sentence_summary(text, limit=3)

    html = ['<div class="insight-dashboard">']
    html.append('<div class="insight-stat-grid">')
    for label, value in [
        ('Words', f'{len(words):,}'),
        ('Lines', f'{len(lines):,}'),
        ('Matches', f'{len(relevant):,}' if prompt else 'Auto'),
    ]:
        html.append(
            '<div class="insight-stat-card">'
            f'<span>{label}</span>'
            f'<strong>{value}</strong>'
            '</div>'
        )
    html.append('</div>')
    if summary:
        html.append('<ul class="insight-clean-list">')
        for sentence in summary[:3]:
            html.append(f'<li>{escape(sentence)}</li>')
        html.append('</ul>')
    html.append('</div>')
    return ''.join(html)


def generate_text_important_details(text, source_name='', prompt=''):
    """Generate concrete facts and notable sections for URL/PDF/DOCX/image text."""
    text = clean_extracted_text(text)
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    if not lines:
        return ''

    words = text.split()
    headings = _extract_document_headings(lines)
    relevant = _relevant_text_lines(lines, prompt, limit=6) if prompt else []
    longest_lines = sorted(lines, key=len, reverse=True)[:3]

    html = ['<div class="important-details">']
    html.append('<div class="detail-grid">')
    for label, value in [
        ('Source', source_name or 'Uploaded content'),
        ('Words', f'{len(words):,}'),
        ('Lines', f'{len(lines):,}'),
        ('First Topic', lines[0][:120]),
    ]:
        html.append(
            '<div class="detail-card">'
            f'<div class="detail-label">{escape(str(label))}</div>'
            f'<div class="detail-value">{escape(str(value))}</div>'
            '</div>'
        )
    html.append('</div>')

    points = relevant or longest_lines
    if points:
        html.append('<h4 class="detail-heading">Important Points</h4>')
        html.append('<ul class="detail-list">')
        for point in points[:6]:
            html.append(f'<li>{escape(point)}</li>')
        html.append('</ul>')

    if headings:
        html.append('<h4 class="detail-heading">Sections Found</h4>')
        html.append('<div class="detail-tags">')
        for heading in headings[:10]:
            html.append(f'<span>{escape(heading)}</span>')
        html.append('</div>')

    html.append('</div>')
    return ''.join(html)


def read_file(file_path):
    ext = os.path.splitext(file_path)[1].lower()
    try:
        # If no extension, try to detect format by reading first bytes
        if not ext:
            with open(file_path, 'rb') as f:
                header = f.read(2048)
            header_str = header.decode('utf-8', errors='ignore')

            # Detect Google Sheets URL saved as file
            google_sheets_match = re.search(
                r'https://docs\.google\.com/spreadsheets/d/([a-zA-Z0-9_-]+)',
                header_str
            )
            if google_sheets_match or 'docs.google.com/spreadsheets' in header_str:
                sheet_id = google_sheets_match.group(1) if google_sheets_match else None
                # Try to extract gid (sheet tab)
                gid_match = re.search(r'gid[=\\]+([0-9]+)', header_str)
                gid = gid_match.group(1) if gid_match else '0'
                if sheet_id:
                    export_url = f'https://docs.google.com/spreadsheets/d/{sheet_id}/export?format=csv&gid={gid}'
                    return read_url(export_url)
                return 'Could not extract Google Sheets ID. Please use File > Share > Publish to web and paste the CSV export URL.'

            # Check for Excel magic bytes
            if header[:2] == b'PK' or header[:8] == b'\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1':
                try:
                    return _coerce_numeric_like_columns(pd.read_excel(file_path))
                except Exception:
                    pass

            # Try CSV
            try:
                return _coerce_numeric_like_columns(pd.read_csv(file_path))
            except Exception:
                pass

            return 'Unsupported file format: No extension detected. Please use .csv, .xlsx, .xls, .pdf, .docx, .txt, or .json files.'

        if ext == '.csv':
            try:
                df = pd.read_csv(file_path)
                # If columns are mostly Unnamed, try to find real header
                unnamed = sum(1 for c in df.columns if str(c).startswith('Unnamed'))
                if unnamed > len(df.columns) / 2:
                    df = smart_read_excel(file_path) if False else pd.read_csv(file_path, header=None)
                    # find best header row
                    best_row, best_score = 0, 0
                    for i in range(min(15, len(df))):
                        score = sum(1 for v in df.iloc[i] if isinstance(v, str) and len(str(v).strip()) > 1)
                        if score > best_score:
                            best_score, best_row = score, i
                    df = pd.read_csv(file_path, header=best_row)
                    df = df.dropna(how='all').dropna(axis=1, how='all').reset_index(drop=True)
                return _coerce_numeric_like_columns(df)
            except Exception as e:
                return 'Error reading CSV: ' + str(e)
        elif ext in ['.xls', '.xlsx']:
            result = smart_read_excel(file_path)
            if hasattr(result, 'columns'):
                result = _dedup_columns(result)
            return result
        elif ext == '.pdf':
            text = _read_pdf_text(file_path)
            if text.startswith('Error'):
                return text
            # If file was actually a CSV saved as .pdf, try reading as DataFrame directly
            try:
                with open(file_path, 'rb') as f:
                    header = f.read(4)
                if not header.startswith(b'%PDF'):
                    try:
                        return pd.read_csv(file_path)
                    except Exception:
                        pass
            except Exception:
                pass
            return text_to_df(text)
        elif ext == '.docx':
            doc = Document(file_path)
            text = '\n'.join([p.text for p in doc.paragraphs])
            for table in doc.tables:
                for row in table.rows:
                    text += '\n' + ' | '.join(cell.text.strip() for cell in row.cells if cell.text.strip())
            return text_to_df(text)
        elif ext in ['.txt', '.md', '.log']:
            with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                return clean_extracted_text(f.read())
        elif ext == '.json':
            import json
            with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                payload = json.load(f)
            if isinstance(payload, list):
                return _dedup_columns(pd.json_normalize(payload).head(5000))
            if isinstance(payload, dict):
                for value in payload.values():
                    if isinstance(value, list):
                        return _dedup_columns(pd.json_normalize(value).head(5000))
                return clean_extracted_text(json.dumps(payload, indent=2))
        elif ext in ['.png', '.jpg', '.jpeg', '.bmp', '.tif', '.tiff', '.webp']:
            return 'Image analysis is not supported. Please upload CSV, Excel, PDF, DOCX, TXT, or JSON files.'
        else:
            return 'Unsupported file format: ' + ext + '. Supported: .csv, .xlsx, .xls, .pdf, .docx, .txt, .json'
    except Exception as e:
        return 'Error reading file: ' + str(e)


def process_prompt(df, prompt, raw_text=None, file_path=None):
    if isinstance(df, str):
        return pdf_text_query(df, prompt) if prompt and prompt.strip() else df
    df = _coerce_numeric_like_columns(_dedup_columns(df))  # always safe
    if not prompt or not prompt.strip():
        return df.head(10)

    # PDF/DOCX readers convert extracted text into a sentence table. Treat it as text for prompts.
    if 'sentence' in df.columns and len(df.columns) <= 3:
        text = '\n'.join(df['sentence'].dropna().astype(str).tolist())
        return pd.DataFrame({'Answer': [pdf_text_query(text, prompt)]})

    direct_answer = answer_dataframe_prompt(df, prompt, file_path=file_path)
    if isinstance(direct_answer, str) and direct_answer == '__INSIGHT__':
        return '__INSIGHT__'
    if isinstance(direct_answer, pd.DataFrame) and not direct_answer.empty:
        return direct_answer

    # Use deterministic pandas logic first for counts, filters, aggregations, and
    # exact lookups. It runs against the full dataset, so it is usually more
    # accurate than an LLM-generated answer for table questions.
    result = rule_based_prompt(df, prompt, file_path=file_path)
    if isinstance(result, str) and result == '__INSIGHT__':
        return '__INSIGHT__'
    if isinstance(result, pd.DataFrame) and not result.empty:
        if 'Result' not in result.columns or not str(result.iloc[0].get('Result', '')).startswith('No results for:'):
            return result

    if raw_text and not _is_table_prompt(prompt):
        answer = pdf_text_query(raw_text, prompt)
        return pd.DataFrame({'Answer': [answer]})

    # Try AI when the deterministic engine cannot understand the request.
    try:
        api_key = os.environ.get('OPENAI_API_KEY', '').strip()
        if api_key and api_key != 'YOUR_API_KEY':
            ai_result = ai_query_smart(df, prompt)
            if isinstance(ai_result, pd.DataFrame) and len(ai_result) > 0:
                if 'AI Error' not in ai_result.columns:
                    return ai_result
    except Exception:
        pass

    if isinstance(result, pd.DataFrame) and result.empty:
        return pd.DataFrame({'Result': [f'No matching data found for: "{prompt}"']})
    return result


def answer_dataframe_prompt(df, prompt, file_path=None):
    """Return direct, user-facing answers for common table questions."""
    import re as _re

    df = _coerce_numeric_like_columns(_dedup_columns(df.copy()))
    p = prompt.lower().strip()
    if not p:
        return None

    scols = [c for c in df.columns if c != '_sheet']
    ncols = df[scols].select_dtypes(include='number').columns.tolist()
    ccols = df[scols].select_dtypes(include=['object', 'string', 'category', 'bool']).columns.tolist()

    def _norm(text):
        return _re.sub(r'[^a-z0-9]+', '', str(text).lower())

    def _col_match(text, candidates=None):
        candidates = candidates or scols
        text_l = str(text).lower()
        text_n = _norm(text)
        for col in candidates:
            if col.lower() == text_l or _norm(col) == text_n:
                return col
        for col in candidates:
            col_l = col.lower()
            col_n = _norm(col)
            if col_l in text_l or col_n in text_n or (text_n and text_n in col_n):
                return col
        return None

    def _mentioned_col(candidates=None):
        candidates = candidates or scols
        for col in candidates:
            col_l = col.lower()
            col_n = _norm(col)
            if col_l in p or (col_n and col_n in _norm(p)):
                return col
        return None

    def _clean_value(value):
        if pd.isna(value):
            return 'N/A'
        if isinstance(value, float):
            return round(value, 4)
        return value

    def _answer(text, **extra):
        payload = {'Answer': [text]}
        for key, value in extra.items():
            payload[key] = [value]
        return pd.DataFrame(payload)

    if any(x in p for x in ['insight', 'full analysis', 'full report', 'complete analysis']):
        return '__INSIGHT__'
    if any(x in p for x in ['analyze', 'analyse']) and ' by ' not in p and not _mentioned_col() and not any(x in p for x in ['where', 'which', 'who', 'count', 'total', 'average', 'sum', 'max', 'min', 'highest', 'lowest', 'largest', 'smallest', 'top', 'bottom', 'row', 'column', 'filter', 'sort']):
        return '__INSIGHT__'

    reg_m = _re.search(r'\b(\d{8,})\b', prompt)
    if reg_m:
        result = rule_based_prompt(df, prompt, file_path=file_path)
        if isinstance(result, pd.DataFrame) and not result.empty:
            return result

    if any(x in p for x in ['how many rows', 'total rows', 'row count', 'number of rows']):
        return _answer(f'The dataset contains {len(df):,} rows.', Rows=len(df))
    if any(x in p for x in ['how many columns', 'total columns', 'column count', 'number of columns']):
        return _answer(f'The dataset contains {len(scols):,} columns.', Columns=len(scols))

    if any(x in p for x in ['column names', 'list columns', 'show columns', 'what columns']):
        return pd.DataFrame({'Column': scols, 'Type': [str(df[c].dtype) for c in scols]})

    if any(x in p for x in ['missing', 'null', 'empty', 'nan']):
        miss = df[scols].isnull().sum()
        total = int(miss.sum())
        if any(x in p for x in ['how many', 'total', 'count']):
            return _answer(f'There are {total:,} missing values in the dataset.', MissingValues=total)
        return pd.DataFrame({
            'Column': scols,
            'Missing Count': miss.values,
            'Missing %': (miss / max(len(df), 1) * 100).round(2).values,
        })

    if 'duplicate' in p:
        duplicate_count = int(df[scols].duplicated().sum())
        if any(x in p for x in ['how many', 'total', 'count', 'number']):
            return _answer(f'There are {duplicate_count:,} duplicate rows.', DuplicateRows=duplicate_count)
        duplicate_rows = df[df[scols].duplicated(keep=False)]
        return duplicate_rows if not duplicate_rows.empty else _answer('No duplicate rows found.', DuplicateRows=0)

    if ' by ' not in p and any(x in p for x in ['average', 'avg', 'mean', 'median', 'sum', 'total', 'maximum', 'highest', 'largest', 'minimum', 'lowest', 'smallest', 'min ', 'max ']):
        metric_col = _mentioned_col(ncols)
        if not metric_col and ncols:
            metric_col = ncols[0]
        if metric_col:
            series = pd.to_numeric(df[metric_col], errors='coerce').dropna()
            if series.empty:
                return _answer(f'Column "{metric_col}" does not contain usable numeric values.')
            if any(x in p for x in ['average', 'avg', 'mean']):
                val = round(float(series.mean()), 4)
                return _answer(f'The average of {metric_col} is {val}.', Column=metric_col, Value=val)
            if 'median' in p:
                val = round(float(series.median()), 4)
                return _answer(f'The median of {metric_col} is {val}.', Column=metric_col, Value=val)
            if any(x in p for x in ['sum', 'total']):
                val = round(float(series.sum()), 4)
                return _answer(f'The total of {metric_col} is {val}.', Column=metric_col, Value=val)
            if any(x in p for x in ['maximum', 'highest', 'largest']) or _re.search(r'\bmax\b', p):
                val = series.max()
                rows = df[pd.to_numeric(df[metric_col], errors='coerce') == val].head(10).copy()
                rows.insert(0, 'Answer', f'The highest {metric_col} is {_clean_value(val)}.')
                return rows
            if any(x in p for x in ['minimum', 'lowest', 'smallest']) or _re.search(r'\bmin\b', p):
                val = series.min()
                rows = df[pd.to_numeric(df[metric_col], errors='coerce') == val].head(10).copy()
                rows.insert(0, 'Answer', f'The lowest {metric_col} is {_clean_value(val)}.')
                return rows

    if ' by ' in p and any(x in p for x in ['count', 'total', 'sum', 'average', 'avg', 'mean', 'median', 'max', 'highest', 'min', 'lowest', 'percentage', 'percent', 'analyze', 'analyse', 'analysis', 'breakdown', 'summary']):
        before_by, after_by = p.split(' by ', 1)
        group_col = _col_match(after_by, scols)
        metric_col = _col_match(before_by, ncols)
        if group_col:
            if any(x in p for x in ['percentage', 'percent']):
                counts = df.groupby(group_col, dropna=False).size().reset_index(name='Count')
                counts['Percentage'] = (counts['Count'] / max(len(df), 1) * 100).round(2)
                return counts.sort_values('Count', ascending=False).reset_index(drop=True)
            if any(x in p for x in ['count', 'how many', 'number']) or not metric_col:
                return df.groupby(group_col, dropna=False).size().reset_index(name='Count').sort_values('Count', ascending=False).reset_index(drop=True)
            values = pd.to_numeric(df[metric_col], errors='coerce')
            grouped_source = df.assign(**{metric_col: values}).dropna(subset=[metric_col])
            if any(x in p for x in ['average', 'avg', 'mean']):
                out = grouped_source.groupby(group_col, dropna=False)[metric_col].mean().round(4).reset_index(name=f'Average {metric_col}')
            elif 'median' in p:
                out = grouped_source.groupby(group_col, dropna=False)[metric_col].median().round(4).reset_index(name=f'Median {metric_col}')
            elif any(x in p for x in ['max', 'highest']):
                out = grouped_source.groupby(group_col, dropna=False)[metric_col].max().reset_index(name=f'Max {metric_col}')
            elif any(x in p for x in ['min', 'lowest']):
                out = grouped_source.groupby(group_col, dropna=False)[metric_col].min().reset_index(name=f'Min {metric_col}')
            else:
                out = grouped_source.groupby(group_col, dropna=False)[metric_col].sum().round(4).reset_index(name=f'Total {metric_col}')
            return out.sort_values(out.columns[-1], ascending=False).reset_index(drop=True)

    count_match = _re.search(r'(?:how many|count|number of)\s+(.+?)(?:\s+(?:students?|records?|rows?|entries|people|items))?$', p)
    if count_match:
        value = count_match.group(1).strip()
        value = _re.sub(r'^(of|the|all)\s+', '', value)
        result = _search_rows_by_value(df, value, ccols)
        if result is not None:
            return _answer(f'There are {len(result):,} matching rows for "{value}".', Query=value, Count=len(result))

    if any(x in p for x in ['show', 'list', 'find', 'filter', 'display', 'give', 'who', 'which']):
        quoted = _re.findall(r'["\']([^"\']+)["\']', prompt)
        search_terms = quoted or _prompt_search_terms(prompt)
        for term in search_terms:
            result = _search_rows_by_value(df, term, ccols)
            if result is not None:
                return result

    return None


def _prompt_search_terms(prompt):
    stopwords = {
        'show', 'find', 'get', 'give', 'list', 'filter', 'where', 'the', 'and',
        'for', 'all', 'data', 'rows', 'with', 'that', 'are', 'is', 'in', 'of',
        'only', 'having', 'from', 'each', 'every', 'sheet', 'excel', 'file',
        'students', 'student', 'records', 'entries', 'values', 'who', 'which',
        'analyze', 'analyse', 'detail', 'details', 'about', 'tell', 'explain',
        'overview', 'me', 'please', 'can', 'you', 'what', 'how', 'result',
        'output', 'table', 'column', 'row', 'number', 'value', 'based', 'using'
    }
    terms = []
    words = re.findall(r'[a-zA-Z0-9&._-]+', prompt)
    for word in words:
        if len(word) > 1 and word.lower() not in stopwords:
            terms.append(word)
    return terms


def _search_rows_by_value(df, value, text_columns):
    value = str(value).strip()
    if not value or len(value) < 2:
        return None
    for col in text_columns:
        series = df[col].astype(str).str.strip()
        mask = series.str.lower() == value.lower()
        if mask.any():
            return df[mask].reset_index(drop=True)
    for col in text_columns:
        mask = df[col].astype(str).str.contains(re.escape(value), case=False, na=False)
        if mask.any():
            return df[mask].reset_index(drop=True)
    return None


def _search_register_number(file_path, reg_num):
    """Search for a register number directly in raw Excel across all sheets."""
    try:
        xl = pd.ExcelFile(file_path)
        reg_str = str(reg_num).strip()
        reg_int = int(float(reg_str))  # handle float representation
        results = []
        for sheet in xl.sheet_names:
            raw = pd.read_excel(file_path, sheet_name=sheet, header=None)
            current_hall = ''
            current_dept = ''
            for i, row in raw.iterrows():
                row_vals = [str(v).strip() if pd.notna(v) else '' for v in row]
                # track hall number
                for v in row_vals:
                    if 'HALL NO' in v.upper():
                        current_hall = v.strip()
                        break
                # track department
                for v in row_vals:
                    if 'YEAR' in v.upper() and len(v) > 3:
                        current_dept = v.replace('\n', ' ').strip()
                        break
                # check each cell for the register number
                for col_idx, v in enumerate(row_vals):
                    v_clean = re.sub(r'\D', '', v)  # digits only
                    if v_clean == reg_str or v_clean == str(reg_int):
                        # get seat from previous column if it looks like A1, B2 etc.
                        seat = ''
                        if col_idx > 0:
                            prev = row_vals[col_idx - 1]
                            if re.match(r'^[A-Z]\d+$', prev):
                                seat = prev
                        results.append({
                            'Register Number': reg_str,
                            'Seat': seat,
                            'Hall Room': current_hall,
                            'Department': current_dept,
                            'Sheet': sheet,
                        })
        if results:
            return pd.DataFrame(results).drop_duplicates().reset_index(drop=True)
        return None
    except Exception:
        return None



def _extract_hall_students(file_path, hall_number):
    records = []
    hall_marker = f'HALL NO - {hall_number}'
    xl = pd.ExcelFile(file_path)
    for sheet in xl.sheet_names:
        raw = pd.read_excel(file_path, sheet_name=sheet, header=None)
        hall_row_idx = None
        for i, row in raw.iterrows():
            if any(isinstance(v, str) and hall_marker in v for v in row):
                hall_row_idx = i
                break
        if hall_row_idx is None:
            continue
        class_labels = {}
        for col_idx, val in enumerate(raw.iloc[hall_row_idx + 1]):
            if isinstance(val, str) and 'YEAR' in val.upper():
                class_labels[col_idx] = val.replace('\n', ' ').strip()
        for i in range(hall_row_idx + 2, len(raw)):
            row_vals = [str(v).strip() if pd.notna(v) else '' for v in raw.iloc[i]]
            if any(kw in row_vals[0].upper() for kw in ['TOTAL', 'ABSENT', 'HALL NO', 'DATE', 'PROFORMA', 'SUPERINTENDENT']):
                break
            for pair_start in range(0, len(row_vals) - 1, 2):
                seat = row_vals[pair_start]
                reg  = row_vals[pair_start + 1]
                if not re.match(r'^[A-Z]\d+$', seat):
                    continue
                reg_clean = re.sub(r'\D', '', reg)
                if len(reg_clean) < 8:
                    continue
                dept = next(
                    (class_labels[c] for c in sorted(class_labels, reverse=True) if c <= pair_start),
                    list(class_labels.values())[0] if class_labels else ''
                )
                records.append({'Hall Room': hall_number, 'Seat': seat,
                                'Register Number': reg_clean, 'Department': dept})
    if not records:
        return None
    return pd.DataFrame(records).drop_duplicates(subset=['Register Number']).reset_index(drop=True)


def _format_metric_value(value):
    """Format values for compact insight cards."""
    try:
        if pd.isna(value):
            return 'N/A'
        if isinstance(value, (int, float)):
            if abs(value) >= 1000:
                return f'{value:,.2f}'.rstrip('0').rstrip('.')
            return f'{value:.2f}'.rstrip('0').rstrip('.')
    except Exception:
        pass
    return escape(str(value))


def _business_label(column_name):
    return escape(str(column_name).replace('_', ' ').strip().title())


def generate_business_insights(df):
    """Generate simple, readable insights from an uploaded dataset."""
    if df is None or not hasattr(df, 'head') or df.empty:
        return ''

    df = _dedup_columns(df.copy())
    num_cols = df.select_dtypes(include='number').columns.tolist()
    cat_cols = df.select_dtypes(include=['object', 'string', 'category', 'bool']).columns.tolist()
    row_count = len(df)
    total_cells = max(df.size, 1)
    missing_total = int(df.isnull().sum().sum())
    missing_pct = round((missing_total / total_cells) * 100, 1)
    complete_pct = round(100 - missing_pct, 1)
    duplicate_count = int(df.duplicated().sum())

    html = ['<div class="insight-dashboard">']
    html.append('<div class="insight-stat-grid">')
    for label, value in [
        ('Rows', f'{row_count:,}'),
        ('Columns', f'{len(df.columns):,}'),
        ('Missing', f'{missing_total:,}'),
        ('Duplicates', f'{duplicate_count:,}'),
    ]:
        html.append(
            '<div class="insight-stat-card">'
            f'<span>{label}</span>'
            f'<strong>{value}</strong>'
            '</div>'
        )
    html.append('</div>')

    html.append('<div class="insight-chart-row">')
    html.append(
        '<div class="insight-pie-card">'
        f'<div class="insight-pie" style="--pct:{complete_pct}%"><span>{complete_pct}%</span></div>'
        '<div><strong>Data Complete</strong><p>Filled cells in this dataset</p></div>'
        '</div>'
    )

    if num_cols:
        col = num_cols[0]
        series = pd.to_numeric(df[col], errors='coerce').dropna()
        if not series.empty:
            html.append(
                '<div class="insight-mini-panel">'
                f'<span>{_business_label(col)}</span>'
                f'<strong>{_format_metric_value(series.mean())}</strong>'
                f'<p>Average | Min {_format_metric_value(series.min())} | Max {_format_metric_value(series.max())}</p>'
                '</div>'
            )
    html.append('</div>')

    if cat_cols and row_count:
        col = cat_cols[0]
        counts = df[col].dropna().astype(str).str.strip()
        counts = counts[counts != ''].value_counts().head(4)
        if not counts.empty:
            html.append('<div class="insight-bars">')
            html.append(f'<div class="insight-bars-title">Top {_business_label(col)}</div>')
            max_count = max(int(counts.iloc[0]), 1)
            for label, count in counts.items():
                pct = round((int(count) / max_count) * 100, 1)
                share = round((int(count) / row_count) * 100, 1)
                html.append(
                    '<div class="insight-bar-row">'
                    f'<span>{escape(str(label))}</span>'
                    '<div class="insight-bar-track">'
                    f'<div class="insight-bar-fill" style="width:{pct}%"></div>'
                    '</div>'
                    f'<strong>{share}%</strong>'
                    '</div>'
                )
            html.append('</div>')

    html.append('</div>')
    return ''.join(html)


def generate_file_important_details(df, source_name=''):
    """Generate concrete file details that complement business insights."""
    if df is None or not hasattr(df, 'head') or df.empty:
        return ''

    df = _coerce_numeric_like_columns(_dedup_columns(df.copy()))
    row_count = len(df)
    col_count = len(df.columns)
    num_cols = df.select_dtypes(include='number').columns.tolist()
    cat_cols = df.select_dtypes(include=['object', 'string', 'category', 'bool']).columns.tolist()
    date_cols = []
    for col in df.columns:
        if col in num_cols:
            continue
        parsed = pd.to_datetime(df[col], errors='coerce')
        if parsed.notna().mean() >= 0.75:
            date_cols.append((col, parsed.min(), parsed.max()))

    html = ['<div class="important-details">']
    html.append('<div class="detail-grid">')
    for label, value in [
        ('Source', source_name or 'Uploaded file'),
        ('Rows', f'{row_count:,}'),
        ('Columns', f'{col_count:,}'),
        ('Numeric Columns', f'{len(num_cols):,}'),
        ('Text Columns', f'{len(cat_cols):,}'),
        ('Missing Cells', f'{int(df.isnull().sum().sum()):,}'),
        ('Duplicate Rows', f'{int(df.duplicated().sum()):,}'),
    ]:
        html.append(
            '<div class="detail-card">'
            f'<div class="detail-label">{escape(str(label))}</div>'
            f'<div class="detail-value">{escape(str(value))}</div>'
            '</div>'
        )
    html.append('</div>')

    important_points = []
    if num_cols:
        totals = df[num_cols].sum(numeric_only=True).sort_values(ascending=False)
        if not totals.empty:
            important_points.append(
                f'Largest total column is {_business_label(totals.index[0])}: {_format_metric_value(totals.iloc[0])}.'
            )
        variances = df[num_cols].std(numeric_only=True).sort_values(ascending=False)
        if not variances.empty and pd.notna(variances.iloc[0]):
            important_points.append(
                f'Most variable numeric column is {_business_label(variances.index[0])}.'
            )
    if cat_cols:
        for col in cat_cols[:2]:
            values = df[col].dropna().astype(str).str.strip()
            values = values[values != '']
            if not values.empty:
                top = values.value_counts().head(1)
                important_points.append(
                    f'Top {_business_label(col)} value is {escape(str(top.index[0]))} with {int(top.iloc[0]):,} records.'
                )
    if date_cols:
        col, start, end = date_cols[0]
        important_points.append(
            f'Date range in {_business_label(col)} runs from {start.date()} to {end.date()}.'
        )

    if important_points:
        html.append('<h4 class="detail-heading">Important Points</h4>')
        html.append('<ul class="detail-list">')
        for point in important_points[:6]:
            html.append(f'<li>{point}</li>')
        html.append('</ul>')

    html.append('<h4 class="detail-heading">Column Details</h4>')
    html.append('<div class="detail-tags">')
    for col in df.columns[:16]:
        dtype = 'number' if col in num_cols else 'date' if any(c == col for c, _, _ in date_cols) else 'text'
        html.append(f'<span>{escape(str(col))} · {dtype}</span>')
    html.append('</div>')
    html.append('</div>')
    return ''.join(html)


def generate_file_insight(df, include_business=True):
    """Generate a complete automatic insight report for any uploaded file."""
    html = []

    # ── 1. Overview ──────────────────────────────────────────────────────────
    num_cols  = df.select_dtypes(include='number').columns.tolist()
    cat_cols  = df.select_dtypes(include=['object', 'string', 'category', 'bool']).columns.tolist()
    dup_count = int(df.duplicated().sum())
    miss_total = int(df.isnull().sum().sum())
    miss_pct  = round(miss_total / max(df.size, 1) * 100, 1)

    html.append('<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:12px;margin-bottom:20px">')
    for label, value, color in [
        ('Total Rows',    f'{len(df):,}',          '#2563eb'),
        ('Total Columns', f'{len(df.columns):,}',  '#475569'),
        ('Numeric Cols',  f'{len(num_cols):,}',    '#059669'),
        ('Text Cols',     f'{len(cat_cols):,}',    '#0284c7'),
        ('Duplicates',    f'{dup_count:,}',         '#dc2626'),
        ('Missing Cells', f'{miss_total:,} ({miss_pct}%)', '#d97706'),
    ]:
        html.append(
            f'<div style="background:#ffffff;border:1px solid #e2e8f0;'
            f'border-radius:10px;padding:14px 16px">'
            f'<div style="font-size:0.72rem;color:#64748b;text-transform:uppercase;'
            f'letter-spacing:0.08em;margin-bottom:6px">{label}</div>'
            f'<div style="font-size:1.3rem;font-weight:800;color:{color}">{value}</div>'
            f'</div>'
        )
    html.append('</div>')

    if include_business:
        html.append(generate_business_insights(df))

    # ── 2. Column Overview Table ──────────────────────────────────────────────
    html.append('<h4 style="color:#2563eb;font-size:0.8rem;text-transform:uppercase;letter-spacing:0.1em;margin:20px 0 10px">📋 Column Overview</h4>')
    html.append('<div style="overflow-x:auto"><table class="data-table">')
    html.append('<thead><tr><th>Column</th><th>Type</th><th>Missing</th><th>Unique</th><th>Sample Values</th></tr></thead><tbody>')
    for col in df.columns:
        dtype    = str(df[col].dtype)
        missing  = int(df[col].isnull().sum())
        miss_p   = round(missing / len(df) * 100, 1) if len(df) else 0
        unique   = int(df[col].nunique())
        samples  = df[col].dropna().astype(str).unique()[:3]
        sample_str = ', '.join(samples)
        miss_color = 'color:#dc2626' if missing > 0 else 'color:#059669'
        html.append(
            f'<tr><td style="font-weight:600">{col}</td>'
            f'<td><span style="font-family:monospace;font-size:0.78rem;background:#f1f5f9;'
            f'padding:2px 7px;border-radius:4px;color:#475569">{dtype}</span></td>'
            f'<td style="{miss_color}">{missing} ({miss_p}%)</td>'
            f'<td>{unique:,}</td>'
            f'<td style="color:#64748b;font-size:0.82rem">{sample_str}</td></tr>'
        )
    html.append('</tbody></table></div>')

    # ── 3. Numeric Statistics ─────────────────────────────────────────────────
    if num_cols:
        html.append('<h4 style="color:#2563eb;font-size:0.8rem;text-transform:uppercase;letter-spacing:0.1em;margin:24px 0 10px">📊 Numeric Statistics</h4>')
        stats = df[num_cols].describe().round(2)
        html.append('<div style="overflow-x:auto">' + stats.to_html(classes='data-table') + '</div>')

    # ── 4. Missing Values ─────────────────────────────────────────────────────
    miss_series = df.isnull().sum()
    miss_cols   = miss_series[miss_series > 0]
    if not miss_cols.empty:
        html.append('<h4 style="color:#d97706;font-size:0.8rem;text-transform:uppercase;letter-spacing:0.1em;margin:24px 0 10px">⚠️ Missing Values</h4>')
        miss_df = pd.DataFrame({
            'Column':  miss_cols.index,
            'Missing': miss_cols.values,
            'Missing %': (miss_cols.values / len(df) * 100).round(1)
        })
        html.append('<div style="overflow-x:auto">' + miss_df.to_html(classes='data-table', index=False) + '</div>')
    else:
        html.append('<p style="color:#059669;margin-top:16px">✅ No missing values found.</p>')

    # ── 5. Duplicate Rows ─────────────────────────────────────────────────────
    if dup_count > 0:
        html.append(f'<p style="color:#dc2626;margin-top:16px">⚠️ <strong>{dup_count}</strong> duplicate rows detected.</p>')
    else:
        html.append('<p style="color:#059669;margin-top:8px">✅ No duplicate rows found.</p>')

    # ── 6. Categorical Value Counts ───────────────────────────────────────────
    if cat_cols:
        html.append('<h4 style="color:#2563eb;font-size:0.8rem;text-transform:uppercase;letter-spacing:0.1em;margin:24px 0 10px">🏷️ Top Values per Text Column</h4>')
        html.append('<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:14px">')
        for col in cat_cols[:6]:
            vc = df[col].value_counts().head(5)
            if vc.empty:
                continue
            html.append('<div style="background:#ffffff;border:1px solid #e2e8f0;border-radius:10px;padding:14px">')
            html.append(f'<div style="font-size:0.8rem;font-weight:700;color:#475569;margin-bottom:10px">{col}</div>')
            for val, cnt in vc.items():
                pct = round(cnt / len(df) * 100, 1)
                html.append(
                    f'<div style="display:flex;justify-content:space-between;align-items:center;'
                    f'margin-bottom:6px;font-size:0.82rem">'
                    f'<span style="color:#475569;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:60%">{val}</span>'
                    f'<span style="color:#2563eb;font-weight:700">{cnt:,} <span style="color:#94a3b8">({pct}%)</span></span>'
                    f'</div>'
                    f'<div style="height:4px;background:#e2e8f0;border-radius:2px;margin-bottom:4px">'
                    f'<div style="height:4px;width:{pct}%;background:#2563eb;border-radius:2px"></div></div>'
                )
            html.append('</div>')
        html.append('</div>')

    # ── 7. Correlation (numeric only) ─────────────────────────────────────────
    if len(num_cols) >= 2:
        html.append('<h4 style="color:#2563eb;font-size:0.8rem;text-transform:uppercase;letter-spacing:0.1em;margin:24px 0 10px">🔗 Correlation Matrix</h4>')
        corr = df[num_cols].corr().round(2)
        html.append('<div style="overflow-x:auto">' + corr.to_html(classes='data-table') + '</div>')

    return ''.join(html)


def rule_based_prompt(df, prompt, file_path=None):
    """Accurate natural-language prompt engine for any DataFrame."""
    import re as _re

    df = _coerce_numeric_like_columns(_dedup_columns(df))
    p      = prompt.lower().strip()
    orig   = prompt.strip()
    scols  = [c for c in df.columns if c != '_sheet']
    ncols  = df[scols].select_dtypes(include='number').columns.tolist()
    ccols  = df[scols].select_dtypes(include=['object', 'string', 'category', 'bool']).columns.tolist()

    # ── helpers ───────────────────────────────────────────────────────────────
    def _nums(default=10):
        ns = _re.findall(r'\d+\.?\d*', p)
        return int(float(ns[0])) if ns else default

    def _col_match(hint):
        """Find best matching column for a hint string."""
        hint = hint.strip().lower()
        hint_norm = _re.sub(r'[^a-z0-9]+', '', hint)
        # exact
        for c in scols:
            if c.lower() == hint:
                return c
        # contains
        for c in scols:
            c_l = c.lower()
            c_norm = _re.sub(r'[^a-z0-9]+', '', c_l)
            if hint in c_l or c_l in hint or (hint_norm and (hint_norm in c_norm or c_norm in hint_norm)):
                return c
        return None

    def _text_search(keyword):
        """Return rows where any cat column matches keyword."""
        kw = keyword.strip().lower()
        if not kw or len(kw) < 2:
            return None
        for col in ccols:
            mask = df[col].astype(str).str.lower() == kw
            if mask.any():
                return df[mask].reset_index(drop=True)
        for col in ccols:
            mask = df[col].astype(str).str.lower().str.contains(
                _re.escape(kw), na=False)
            if mask.any():
                return df[mask].reset_index(drop=True)
        return None

    def _num_filter(col, op, val):
        ops = {'>'  : df[col] > val,  '>=' : df[col] >= val,
               '<'  : df[col] < val,  '<=' : df[col] <= val,
               '='  : df[col] == val, '==' : df[col] == val,
               '!=' : df[col] != val}
        mask = ops.get(op)
        if mask is None:
            return None
        result = df[mask]
        return result if not result.empty else pd.DataFrame(
            {'Result': [f'No rows where {col} {op} {val}']})

    # ── 0. Register number search (8+ digit number in prompt) ─────────────────
    reg_m = re.search(r'\b(\d{8,})\b', prompt)
    if reg_m:
        reg_num = reg_m.group(1)
        # search in raw file first (most accurate for seating Excel)
        if file_path and os.path.exists(file_path):
            result = _search_register_number(file_path, reg_num)
            if result is not None and not result.empty:
                return result
        # fallback: search all columns in the flattened df
        for col in scols:
            mask = df[col].astype(str).str.contains(reg_num, na=False)
            if mask.any():
                return df[mask].reset_index(drop=True)
            # also try matching as integer (handles float storage like 1.152302e+10)
            try:
                reg_int = int(float(reg_num))
                mask2 = df[col].apply(
                    lambda x: str(int(float(x))) == str(reg_int)
                    if pd.notna(x) and str(x).replace('.','').replace('e+','').replace('E+','').isdigit()
                    else False
                )
                if mask2.any():
                    return df[mask2].reset_index(drop=True)
            except Exception:
                pass
        return pd.DataFrame({'Result': [f'Register number {reg_num} not found in this file.']})

    # ── 1. Hall room ──────────────────────────────────────────────────────────
    hm = _re.search(r'hall\s*(?:no|number|room)?[:\s#-]*(\d{3,})', p)
    if hm:
        hall_num = int(hm.group(1))
        if file_path and os.path.exists(file_path):
            result = _extract_hall_students(file_path, hall_num)
            if result is not None and not result.empty:
                return result
            xl = pd.ExcelFile(file_path)
            return pd.DataFrame({'Result': [
                f'Hall {hall_num} not found. Sheets: {xl.sheet_names}']})
        for col in ccols:
            mask = df[col].astype(str).str.contains(str(hall_num), na=False)
            if mask.any():
                return df[mask].reset_index(drop=True)

    # ── 2. Show all ───────────────────────────────────────────────────────────
    if any(x in p for x in ['show all', 'all data', 'entire data', 'full data', 'all rows']):
        return df

    # ── 3. Insight (pure — no filter words) ───────────────────────────────────
    _fw = {'show','filter','only','where','find','get','list','students',
           'student','who','which','with','having','above','below','less',
           'greater','more','than','equal','between','like','containing'}
    _has_fw = any(w in p.split() for w in _fw)
    if any(x in p for x in ['insight','full analysis','full report','complete analysis']):
        return '__INSIGHT__'
    if any(x in p for x in ['analyze','analyse']) and not _has_fw:
        return '__INSIGHT__'

    # ── 4. Describe / statistics ──────────────────────────────────────────────
    if any(x in p for x in ['describe','summary','statistics','stat','profile','info']):
        return df[scols].describe(include='all').reset_index()

    # ── 5. Shape ──────────────────────────────────────────────────────────────
    if any(x in p for x in ['shape','dimension','size of']):
        return pd.DataFrame({'Rows': [len(df)], 'Columns': [len(df.columns)]})

    # ── 6. Row / column counts ────────────────────────────────────────────────
    if any(x in p for x in ['how many rows','total rows','row count','number of rows','count rows']):
        return pd.DataFrame({'Total Rows': [len(df)]})
    if any(x in p for x in ['how many columns','total columns','number of columns','count columns']):
        return pd.DataFrame({'Total Columns': [len(df.columns)]})

    # ── 7. Column names ───────────────────────────────────────────────────────
    if any(x in p for x in ['column names','list columns','show columns','what columns','columns list']):
        return pd.DataFrame({'Column': scols,
                             'Type': [str(df[c].dtype) for c in scols]})

    # ── 8. Missing values ─────────────────────────────────────────────────────
    if any(x in p for x in ['missing','null','nan','empty','na values']):
        miss = df[scols].isnull().sum()
        pct  = (miss / len(df) * 100).round(2)
        return pd.DataFrame({'Column': scols,
                             'Missing Count': miss.values,
                             'Missing %': pct.values})

    # ── 9. Duplicates ─────────────────────────────────────────────────────────
    if 'duplicate' in p:
        dups = df[df[scols].duplicated(keep=False)]
        return dups if not dups.empty else pd.DataFrame(
            {'Result': ['No duplicate rows found.']})

    # ── 10. Head / tail / sample ──────────────────────────────────────────────
    if any(x in p for x in ['head','first','top rows','show first','initial']):
        return df.head(_nums(10))
    if any(x in p for x in ['tail','last','bottom rows','show last','final']):
        return df.tail(_nums(10))
    if _re.search(r'\bsample\b', p):
        return df.sample(min(_nums(10), len(df)))

    # ── 11. Sort ──────────────────────────────────────────────────────────────
    if any(x in p for x in ['sort','order by','arrange','rank']):
        asc = not any(x in p for x in ['desc','descending','highest','largest'])
        for col in scols:
            if col.lower() in p:
                return df.sort_values(by=col, ascending=asc).reset_index(drop=True)
        if ncols:
            return df.sort_values(by=ncols[0], ascending=asc).reset_index(drop=True)

    # ── 12. Group by / count by ───────────────────────────────────────────────
    if any(x in p for x in ['group by','count by','frequency','distribution',
                             'value counts','breakdown']):
        for col in scols:
            if col.lower() in p:
                vc = df[col].value_counts().reset_index()
                vc.columns = [col, 'Count']
                return vc
        if ccols:
            vc = df[ccols[0]].value_counts().reset_index()
            vc.columns = [ccols[0], 'Count']
            return vc

    # Aggregate metric by category, e.g. "total sales by region" or "average marks by class"
    if ' by ' in p and any(x in p for x in ['total', 'sum', 'average', 'avg', 'mean', 'max', 'maximum', 'min', 'minimum', 'count']):
        group_col = None
        metric_col = None
        after_by = p.split(' by ', 1)[1]
        before_by = p.split(' by ', 1)[0]
        after_norm = _re.sub(r'[^a-z0-9]+', '', after_by)
        before_norm = _re.sub(r'[^a-z0-9]+', '', before_by)
        for col in scols:
            col_norm = _re.sub(r'[^a-z0-9]+', '', col.lower())
            if col.lower() in after_by or col_norm in after_norm:
                group_col = col
                break
        for col in ncols:
            col_norm = _re.sub(r'[^a-z0-9]+', '', col.lower())
            if col.lower() in before_by or col_norm in before_norm:
                metric_col = col
                break
        if group_col:
            if any(x in p for x in ['count', 'how many', 'number of']) or not metric_col:
                grouped = df.groupby(group_col, dropna=False).size().reset_index(name='Count')
                return grouped.sort_values('Count', ascending=False).reset_index(drop=True)
            if any(x in p for x in ['average', 'avg', 'mean']):
                grouped = df.groupby(group_col, dropna=False)[metric_col].mean().round(4).reset_index(name=f'Average {metric_col}')
            elif any(x in p for x in ['max', 'maximum', 'highest']):
                grouped = df.groupby(group_col, dropna=False)[metric_col].max().reset_index(name=f'Max {metric_col}')
            elif any(x in p for x in ['min', 'minimum', 'lowest']):
                grouped = df.groupby(group_col, dropna=False)[metric_col].min().reset_index(name=f'Min {metric_col}')
            else:
                grouped = df.groupby(group_col, dropna=False)[metric_col].sum().reset_index(name=f'Total {metric_col}')
            value_col = grouped.columns[-1]
            return grouped.sort_values(value_col, ascending=False).reset_index(drop=True)

    # ── 13. Aggregations ──────────────────────────────────────────────────────
    if any(x in p for x in ['average','mean','avg']):
        col = _col_match(' '.join(
            w for w in p.split() if w not in {'average','mean','avg','of','the'}))
        if col and col in ncols:
            return pd.DataFrame({'Column':[col],'Mean':[round(df[col].mean(),4)]})
        r = df[ncols].mean().round(4).reset_index()
        r.columns = ['Column','Mean']
        return r

    if _re.search(r'\bsum\b', p):
        col = _col_match(' '.join(
            w for w in p.split() if w not in {'sum','of','the','total'}))
        if col and col in ncols:
            return pd.DataFrame({'Column':[col],'Sum':[round(df[col].sum(),4)]})
        r = df[ncols].sum().reset_index(); r.columns = ['Column','Sum']
        return r

    if any(x in p for x in ['maximum','highest','largest']) or _re.search(r'\bmax\b', p):
        for col in ncols:
            if col.lower() in p:
                val = df[col].max()
                row = df[df[col]==val].head(1)
                return row if not row.empty else pd.DataFrame({'Column':[col],'Max':[val]})
        r = df[ncols].max().reset_index(); r.columns = ['Column','Max']
        return r

    if any(x in p for x in ['minimum','lowest','smallest']) or _re.search(r'\bmin\b', p):
        for col in ncols:
            if col.lower() in p:
                val = df[col].min()
                row = df[df[col]==val].head(1)
                return row if not row.empty else pd.DataFrame({'Column':[col],'Min':[val]})
        r = df[ncols].min().reset_index(); r.columns = ['Column','Min']
        return r

    # ── 14. Unique / distinct ─────────────────────────────────────────────────
    if any(x in p for x in ['unique','distinct']):
        for col in scols:
            if col.lower() in p:
                return pd.DataFrame({f'{col} (unique)': df[col].dropna().unique()})
        return pd.DataFrame({'Column': scols,
                             'Unique Count': [df[c].nunique() for c in scols]})

    # ── 15. Correlation ───────────────────────────────────────────────────────
    if any(x in p for x in ['correlation','corr','relationship']):
        return df[ncols].corr().round(3)

    # ── 16. Natural language numeric filters ──────────────────────────────────
    # Patterns: "greater than", "more than", "above", "over",
    #           "less than", "below", "under", "at least", "at most",
    #           "equal to", "not equal", "col > val", "col = val"

    NL_OPS = [
        (r'(?:greater than or equal to|at least|>=)\s*([\d.]+)',  '>='),
        (r'(?:less than or equal to|at most|<=)\s*([\d.]+)',      '<='),
        (r'(?:greater than|more than|above|over|>)\s*([\d.]+)',   '>' ),
        (r'(?:less than|below|under|fewer than|<)\s*([\d.]+)',    '<' ),
        (r'(?:not equal to|!=)\s*([\d.]+)',                       '!='),
        (r'(?:equal to|equals?|=|==)\s*([\d.]+)',                 '=='),
    ]

    for pattern, op in NL_OPS:
        m = _re.search(pattern, p)
        if m:
            val = float(m.group(1))
            # find which column the prompt refers to
            col = None
            for c in ncols:
                if c.lower() in p:
                    col = c
                    break
            if col is None and ncols:
                col = ncols[0]   # default to first numeric col
            if col:
                result = _num_filter(col, op, val)
                if result is not None:
                    return result

    # Symbol-based filter: "col > val", "col = val"
    sym_m = _re.search(
        r'([a-zA-Z][\w\s]*?)\s*(>=|<=|!=|>|<|==|=)\s*([\d.]+)', orig)
    if sym_m:
        col_hint = sym_m.group(1).strip().lower()
        op, val  = sym_m.group(2).strip(), float(sym_m.group(3))
        col = _col_match(col_hint)
        if col and col in ncols:
            result = _num_filter(col, op, val)
            if result is not None:
                return result

    # ── 17. Between X and Y ───────────────────────────────────────────────────
    bm = _re.search(r'between\s+([\d.]+)\s+and\s+([\d.]+)', p)
    if bm:
        lo, hi = float(bm.group(1)), float(bm.group(2))
        for col in ncols:
            if col.lower() in p:
                return df[(df[col] >= lo) & (df[col] <= hi)]
        if ncols:
            return df[(df[ncols[0]] >= lo) & (df[ncols[0]] <= hi)]

    # ── 18. Top N / Bottom N ──────────────────────────────────────────────────
    tn = _re.search(r'top\s+(\d+)', p)
    if tn:
        n = int(tn.group(1))
        for col in ncols:
            if col.lower() in p:
                return df.nlargest(n, col).reset_index(drop=True)
        if ncols:
            return df.nlargest(n, ncols[0]).reset_index(drop=True)

    bn = _re.search(r'bottom\s+(\d+)', p)
    if bn:
        n = int(bn.group(1))
        for col in ncols:
            if col.lower() in p:
                return df.nsmallest(n, col).reset_index(drop=True)
        if ncols:
            return df.nsmallest(n, ncols[0]).reset_index(drop=True)

    # ── 19. Select specific columns ───────────────────────────────────────────
    if any(x in p for x in ['select','show only','display only','only show','only columns']):
        matched = [c for c in scols if c.lower() in p]
        if matched:
            return df[matched]

    # ── 20. Count how many X ──────────────────────────────────────────────────
    cm = _re.search(
        r'(?:how many|count of?|number of)\s+(.+?)(?:\s+(?:students?|records?|rows?|patients?|people|entries))?$',
        p)
    if cm:
        val_hint = cm.group(1).strip()
        # check if it's a numeric condition
        for pattern, op in NL_OPS:
            nm = _re.search(pattern, val_hint)
            if nm:
                val = float(nm.group(1))
                col = None
                for c in ncols:
                    if c.lower() in val_hint:
                        col = c; break
                if col is None and ncols:
                    col = ncols[0]
                if col:
                    mask = _num_filter(col, op, val)
                    if isinstance(mask, pd.DataFrame) and 'Result' not in mask.columns:
                        return pd.DataFrame({'Count': [len(mask)],
                                             'Condition': [f'{col} {op} {val}']})
        # text search
        result = _text_search(val_hint)
        if result is not None and not result.empty:
            return pd.DataFrame({'Query': [val_hint], 'Count': [len(result)]})

    # ── 21. "X students/patients/records" pattern ─────────────────────────────
    dm = _re.search(
        r'(?:show|list|get|find|filter|display|give)?\s*(.+?)\s+'
        r'(?:students?|records?|entries|rows|data|patients?|people|cases?)',
        p)
    if dm:
        val_hint = dm.group(1).strip()
        skip = {'all','the','only','some','any','those','these','male','female',
                'top','bottom','first','last','high','low','with','having'}
        if val_hint and val_hint not in skip and len(val_hint) > 1:
            result = _text_search(val_hint)
            if result is not None and not result.empty:
                return result

    # ── 22. "who said/answered/have X" ───────────────────────────────────────
    who_m = _re.search(r'who\s+(?:said|answered|have|has|is|are|=|participated)\s+["\']?([\w\s&.]+)["\']?', p)
    if who_m:
        result = _text_search(who_m.group(1).strip())
        if result is not None and not result.empty:
            return result

    # ── 23. "is/are/= value" filter ───────────────────────────────────────────
    is_m = _re.search(r'(?:\bis\b|\bare\b|equals?)\s+["\']?([\w&.\s]+)["\']?', p)
    if is_m:
        result = _text_search(is_m.group(1).strip())
        if result is not None and not result.empty:
            return result

    # ── 24. Numeric value in prompt — filter that column ─────────────────────
    # e.g. "show Gender 1", "Gender 0 patients"
    nv_m = _re.search(r'([a-zA-Z][\w\s]*?)\s+(\d+\.?\d*)\b', p)
    if nv_m:
        col_hint = nv_m.group(1).strip()
        val      = float(nv_m.group(2))
        col = _col_match(col_hint)
        if col and col in ncols:
            result = _num_filter(col, '==', val)
            if result is not None:
                return result

    # ── 25. Smart keyword search ──────────────────────────────────────────────
    stopwords = {
        'show','find','get','give','list','filter','where','the','and','for',
        'all','data','rows','with','that','are','is','in','of','only','having',
        'from','each','every','sheet','excel','file','students','student',
        'records','entries','values','who','which','analyze','analyse','detail',
        'details','about','tell','explain','overview','me','please','can','you',
        'what','how','result','output','table','column','row','number','value',
        'based','using','their','them','these','those','into','onto','patients',
        'patient','people','person','record','entry','cases','case','high','low',
        'above','below','than','more','less','greater','equal','not','have','has',
        'been','was','were','will','would','could','should','may','might','must',
        'also','just','very','much','many','some','any','no','yes','true','false'
    }
    raw_kws = _re.findall(r'[a-zA-Z0-9&.]+', prompt.lower())
    keywords = [w for w in raw_kws if len(w) > 1 and w not in stopwords]

    for kw in keywords:
        result = _text_search(kw)
        if result is not None and not result.empty:
            return result

    # numeric column stats if column name mentioned
    for col in ncols:
        if col.lower() in p:
            stats = df[col].describe().round(4)
            return stats.reset_index().rename(
                columns={'index': 'Stat', col: 'Value'})

    # ── 26. Nothing matched — helpful message ─────────────────────────────────
    col_list = ', '.join(scols[:8])
    sample_vals = {}
    for c in ccols[:3]:
        sample_vals[c] = df[c].dropna().unique()[:3].tolist()
    hints = '; '.join(f'{c}: {v}' for c, v in sample_vals.items())
    return pd.DataFrame({'Result': [
        f'No results for: "{prompt}". '
        f'Columns: {col_list}. '
        + (f'Sample values — {hints}. ' if hints else '')
        + 'Try: "show first 10", "describe", "filter <col> > <val>", or a value from your data.'
    ]})


def ai_query_smart(df, prompt):
    api_key = os.environ.get('OPENAI_API_KEY', '').strip()
    client = openai.OpenAI(api_key=api_key)

    sample_size = min(100, len(df))
    search_cols = [c for c in df.columns if c != '_sheet']
    col_list = ', '.join(df.columns.tolist())
    sample_str = df.head(sample_size).to_string()

    val_counts = ''
    for col in df[search_cols].select_dtypes(include=['object', 'string', 'category', 'bool']).columns[:8]:
        vc = df[col].dropna().astype(str).str.strip()
        vc = vc[vc != ''].value_counts().head(15)
        if not vc.empty:
            val_counts += f"  '{col}' values: {vc.to_dict()}\n"

    sheet_info = ''
    if '_sheet' in df.columns:
        sheet_info = f"NOTE: '_sheet' column indicates which Excel sheet each row came from. Sheets: {df['_sheet'].unique().tolist()}\n"

    data_info = (
        f"Shape: {df.shape[0]} rows x {df.shape[1]} columns\n"
        f"Columns: {col_list}\n"
        f"{sheet_info}"
        f"Dtypes: {df.dtypes.to_dict()}\n"
        f"Sample data ({sample_size} rows):\n{sample_str}\n"
        f"Categorical value counts:\n{val_counts}"
    )

    system_msg = (
        "You are a Python/pandas expert. Given a DataFrame called 'df' and a user query, "
        "write pandas code to answer it exactly.\n"
        "STRICT RULES:\n"
        "1. Output ONLY valid Python — no markdown fences, no comments, no explanation\n"
        "2. Last line must be an expression that returns a DataFrame or Series\n"
        "3. Use 'df' as the variable name\n"
        "4. For text search use: df[df['col'].astype(str).str.contains('val', case=False, na=False)]\n"
        "5. For numeric filters use: df[df['col'] > value]\n"
        "6. For groupby: df.groupby('col').size().reset_index(name='Count')\n"
        "7. Only use pd and np — no imports\n"
        "8. If query is about counting/classifying students by year/class, search ALL columns for matching text\n"
        "9. For 'classify' or 'list' queries, return the full matching rows"
    )

    response = client.chat.completions.create(
        model='gpt-4o-mini',
        messages=[
            {'role': 'system', 'content': system_msg},
            {'role': 'user', 'content': f"{data_info}\n\nUser Query: {prompt}"}
        ],
        temperature=0,
        max_tokens=500
    )

    code = response.choices[0].message.content.strip()
    code = re.sub(r'```python|```', '', code).strip()

    import numpy as np
    local_vars = {'df': df.copy(), 'pd': pd, 'np': np}
    try:
        lines = [l for l in code.strip().splitlines() if l.strip()]
        if len(lines) > 1:
            exec('\n'.join(lines[:-1]), local_vars)
        result = eval(lines[-1], local_vars)
        if isinstance(result, pd.Series):
            result = result.reset_index()
            result.columns = [str(c) for c in result.columns]
        if isinstance(result, pd.DataFrame):
            return result
        if isinstance(result, (int, float, str, bool)):
            return pd.DataFrame({'Result': [str(result)]})
        return pd.DataFrame({'Result': [str(result)]})
    except Exception as e:
        # Fallback to rule-based
        fallback = rule_based_prompt(df, prompt)
        if isinstance(fallback, pd.DataFrame) and len(fallback) > 0:
            return fallback
        return pd.DataFrame({'AI Error': [str(e)], 'Generated Code': [code]})
