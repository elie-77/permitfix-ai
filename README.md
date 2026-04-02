# 🏗️ PermitFix AI

An AI-powered building permit compliance assistant. Upload permit documents and architectural drawings, then ask questions to get detailed code compliance analysis.

## Features

- **Project Dashboard** — organize permits by project, track status (In Review, Corrections Needed, Approved, On Hold)
- **Document Upload** — upload PDFs (permit letters, correction notices, code sheets)
- **Drawing Analysis** — upload PNG, JPG, WebP images of floor plans, elevations, and site plans for visual AI analysis
- **AI Chat** — ask questions about permits, code requirements, and corrections with citations to IBC, CBC, OBC, NFPA, ADA, and more
- **User Accounts** — each user has their own private project space

## Setup (Local)

**1. Clone the repo**
```bash
git clone https://github.com/YOUR_USERNAME/permitfix-ai.git
cd permitfix-ai
```

**2. Create a virtual environment**
```bash
python3 -m venv venv
source venv/bin/activate
```

**3. Install dependencies**
```bash
pip install -r requirements.txt
```

**4. Add your Anthropic API key**
```bash
cp .env.example .env
# Open .env and replace the placeholder with your key from console.anthropic.com
```

**5. Run the app**
```bash
streamlit run app.py
```

Open `http://localhost:8501` in your browser.

## Deployment (Streamlit Cloud)

1. Push this repo to GitHub
2. Go to [share.streamlit.io](https://share.streamlit.io) and connect your repo
3. Add your API key under **Settings → Secrets**:
```toml
ANTHROPIC_API_KEY = "sk-ant-..."
```

## Environment Variables

| Variable | Description |
|---|---|
| `ANTHROPIC_API_KEY` | Your API key from [console.anthropic.com](https://console.anthropic.com) |

## Tech Stack

- [Streamlit](https://streamlit.io) — UI framework
- [Anthropic Claude](https://anthropic.com) — AI model (Claude Opus 4.6)
- [pdfplumber](https://github.com/jsvine/pdfplumber) — PDF text extraction
- [bcrypt](https://github.com/pyca/bcrypt) — password hashing
