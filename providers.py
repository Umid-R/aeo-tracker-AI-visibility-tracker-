"""One function per AI provider.

Every function has the same contract:
    input:  model_name (str), question (str)
    output: (answer_text, input_tokens, output_tokens)

run.py only knows this contract, so adding a provider = add one function + one dict entry.
"""
import os

from google import genai

_gemini_client = None


def _gemini():
    global _gemini_client
    if _gemini_client is None:
        _gemini_client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
    return _gemini_client


def ask_gemini(model_name: str, question: str):
    resp = _gemini().models.generate_content(model=model_name, contents=question)
    usage = resp.usage_metadata
    return resp.text, usage.prompt_token_count, usage.candidates_token_count


# provider name in config.yaml -> function
PROVIDERS = {
    "gemini": ask_gemini,
    # "openai": ask_openai,   # step 1b
}
