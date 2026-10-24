# Solar Atlas — beginner-friendly prototype

This is a working Streamlit prototype for an AI-assisted Solar Energy Potential Mapping hackathon idea.

## Important limitations
- The app uses manual assumptions and synthetic map points.
- It does not yet connect to Bhuvan or VEDAS.
- It does not yet identify real rooftops or run a trained AI model.
- Solar calculations are illustrative estimates, not a site survey or guaranteed savings.

## Windows + VS Code setup

### 1. Install Python
Install a recent Python version from https://www.python.org/downloads/ .
During installation, tick **Add Python to PATH** if the installer offers that option.

### 2. Open this project in VS Code
Extract the ZIP file. In VS Code choose **File → Open Folder** and select the `solar_potential_mapper` folder.

### 3. Open the terminal
In VS Code choose **Terminal → New Terminal**.

### 4. Create a virtual environment
Run:
```powershell
python -m venv .venv
```

### 5. Activate it
For PowerShell:
```powershell
.venv\Scripts\Activate.ps1
```
If PowerShell blocks activation, use Command Prompt terminal and run:
```bat
.venv\Scripts\activate.bat
```

### 6. Install libraries
```powershell
python -m pip install --upgrade pip
pip install -r requirements.txt
```

### 7. Run the app
```powershell
python -m streamlit run frontend/app.py
```
A browser tab should open. If it does not, open the local address printed in the terminal, usually `http://localhost:8501`.

### 8. Stop the app
Click the terminal and press `Ctrl+C`.

## Pages
- **Dashboard** — project overview and limitations
- **Solar Calculator** — estimate usable roof area, capacity, yearly energy, and potential energy value
- **Sample GIS Map** — map of synthetic points around a chosen coordinate, with CSV download
- **About & Next Steps** — roadmap for real Bhuvan/VEDAS and AI integration

## If `python` is not recognized
Try `py -m venv .venv` and `py -m pip install -r requirements.txt`, then run `py -m streamlit run app.py`.

## Next phase
Before adding live data, select the exact Bhuvan/VEDAS layer/service, check access and usage terms, and confirm its data format. Then add appropriate raster/vector GIS processing and validation.
