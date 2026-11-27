import time, yaml
from dotenv import load_dotenv
from providers import ask_gemini

load_dotenv()
cfg = yaml.safe_load(open("config.yaml", encoding="utf-8"))
model = cfg["models"][0]["name"]

for q in cfg["questions"]:
    print(f"\n===== {q['id']}: {q['text']} =====")
    try:
        text, tin, tout = ask_gemini(model, q["text"])
        print(text)
        print(f"[{tin} in / {tout} out tokens]")
    except Exception as e:
        print("ERROR:", e)
    time.sleep(4)