# Bland AI Voice Agent - Custom Voice Only

This version hides all voice selection fields from the UI. The app always uses your custom Bland voice ID from `main.py` or `.env`.

## Run locally

```powershell
python -m venv venv
.\venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
uvicorn main:app --reload
```

Open: http://127.0.0.1:8000

## Environment
Create `.env`:

```env
BLAND_API_KEY=your_bland_api_key_here
BLAND_BASE_URL=https://api.bland.ai
CUSTOM_VOICE_ID=13a1a524-4515-4f96-a57a-aa142697972e
PYTHON_VERSION=3.11.9
```


Update: Speech Model and Language fields are removed from the UI. Language is fixed in backend as en-IN.
