import subprocess, sys
result = subprocess.run(
    [sys.executable, "gradio_app.py"],
    capture_output=True, text=True, encoding="utf-8", errors="replace",
    cwd=r"C:\Users\Kanyun\.qclaw\workspace\mesa-econ",
    timeout=15
)
print("STDOUT:", result.stdout[-2000:] if result.stdout else "(empty)")
print("STDERR:", result.stderr[-3000:] if result.stderr else "(empty)")
print("RC:", result.returncode)
