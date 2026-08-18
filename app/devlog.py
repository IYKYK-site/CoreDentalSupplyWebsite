from datetime import datetime


def devlog(category: str, message: str):
    timestamp = datetime.now().strftime("%H:%M:%S")
    print(f"[{timestamp}] [{category}] {message}", flush=True)