import subprocess
import sys
import os
import time

os.chdir("C:/Users/rdpadmin/produtos-imgs")

# Matar qualquer processo Flask existente
try:
    import psutil
    for proc in psutil.process_iter(['pid', 'cmdline']):
        try:
            cmdline = proc.info.get('cmdline') or []
            if cmdline and 'app.py' in ' '.join(cmdline):
                print(f"Matando Flask PID {proc.info['pid']}")
                proc.terminate()
        except Exception:
            pass
    time.sleep(2)
except ImportError:
    # Fallback: usar taskkill
    subprocess.run(["taskkill", "/F", "/IM", "python.exe"], capture_output=True)
    time.sleep(2)

# Iniciar Flask
log_out = open("flask.log", "w")
log_err = open("flask_err.log", "w")

proc = subprocess.Popen(
    [sys.executable, "app.py"],
    stdout=log_out,
    stderr=log_err,
    creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
)

print(f"Flask iniciado com PID {proc.pid}")
print(f"Log: flask.log / flask_err.log")

# Aguardar um pouco para Flask iniciar
time.sleep(8)

# Verificar se Flask responde
import requests
try:
    r = requests.get("http://localhost:5050/", timeout=5)
    print(f"✓ Flask respondendo: HTTP {r.status_code}")
except Exception as e:
    print(f"✗ Flask não responde: {e}")
    print("Verifique flask_err.log para detalhes")
