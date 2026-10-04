# SheetAI - AI-Powered Data Analysis Platform

## Features
- Upload CSV, Excel, PDF, DOCX, TXT, and JSON files
- Analyze data from URLs (CSV, Excel, Wikipedia tables)
- Compare datasets (File vs URL)
- AI-powered natural language queries
- Data quality reports
- Clean noise and duplicates
- Manual data editing

## Setup Instructions

### 1. Install Dependencies
```bash
pip install -r requirements.txt
```

If Python was installed from the Microsoft Store and the virtual environment
fails with `Unable to create process`, install Python from https://python.org,
then rebuild the environment:

```powershell
cd C:\Users\msjay\Downloads\sheetai-main
Remove-Item -Recurse -Force .venv
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
cd .\sheetai-main
pip install -r requirements.txt
```

### 2. Set OpenAI API Key (Optional but Recommended)

For AI-powered prompt analysis, set your OpenAI API key as an environment variable:

**Windows (Command Prompt):**
```cmd
set OPENAI_API_KEY=your-api-key-here
```

**Windows (PowerShell):**
```powershell
$env:OPENAI_API_KEY="your-api-key-here"
```

**Linux/Mac:**
```bash
export OPENAI_API_KEY=your-api-key-here
```

**Permanent Setup (Windows):**
1. Search "Environment Variables" in Windows
2. Click "Environment Variables" button
3. Under "User variables", click "New"
4. Variable name: `OPENAI_API_KEY`
5. Variable value: your API key
6. Click OK

### 3. Run Migrations
```bash
python manage.py migrate
```

### 4. Create Superuser
```bash
python manage.py createsuperuser
```

### 5. Run Server
```bash
python manage.py runserver
```

### 6. Access Application
- Main App: http://127.0.0.1:8000/
- Admin Panel: http://127.0.0.1:8000/admin/

## Usage Examples

### Natural Language Queries (with OpenAI API key):
- "Show rows where sales > 5000"
- "Sort by price descending"
- "Count by category"
- "Show missing values"
- "Filter age between 25 and 40"
- "Group by country and sum revenue"
- "Find top 10 customers by spending"

### Without API Key (Rule-based):
- "show all data"
- "describe" or "summary"
- "head 20" or "first 10"
- "tail 15" or "last 5"
- "sales > 1000"
- "price < 50"
- "sort by name"
- "count by category"
- "missing values"

## Compare Feature
Upload a file AND paste a URL simultaneously to compare:
- Row/column counts
- Missing values
- Duplicates
- Common vs unique columns
- Statistical differences

## Notes
- Without OpenAI API key, the system uses rule-based query processing
- With API key, you get full natural language understanding
- Get your API key from: https://platform.openai.com/api-keys

## Password Reset Email

For "Forgot password?" emails, set Gmail SMTP environment variables before running the server.
Use a Gmail App Password, not your normal Gmail password.

**Windows PowerShell:**
```powershell
$env:EMAIL_HOST_USER="yourgmail@gmail.com"
$env:EMAIL_HOST_PASSWORD="your-gmail-app-password"
```

Each user must have an email address saved in the admin panel for password reset to work.

   
"# sheetai-main" 
"# sheetai-main" 
