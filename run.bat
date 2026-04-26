@echo off
echo ============================================
echo  Binance Perp Scanner - Starting...
echo ============================================
echo.
echo  Opening in your browser at http://localhost:8501
echo  Press Ctrl+C in this window to stop.
echo.
python -m streamlit run scanner.py --server.headless false
pause
