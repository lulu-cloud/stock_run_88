"""DeepSeek v4 pro 客户端（OpenAI 兼容接口）"""

from typing import Generator
from openai import OpenAI
from backend.config import DEEPSEEK_API_KEY, DEEPSEEK_BASE_URL, DEEPSEEK_MODEL


def get_client() -> OpenAI:
    """获取 DeepSeek 客户端"""
    return OpenAI(
        api_key=DEEPSEEK_API_KEY,
        base_url=DEEPSEEK_BASE_URL,
    )


def chat(system_prompt: str, user_message: str, temperature: float = 0.3) -> str:
    """发送对话请求，返回文本回复"""
    client = get_client()
    response = client.chat.completions.create(
        model=DEEPSEEK_MODEL,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ],
        stream=False,
        reasoning_effort="high",
        extra_body={"thinking": {"type": "enabled"}},
        temperature=temperature,
    )
    return response.choices[0].message.content


def chat_stream(system_prompt: str, user_message: str, temperature: float = 0.3) -> Generator[str, None, str]:
    """流式对话，yield 每个 token 片段，最后 return 完整文本"""
    client = get_client()
    response = client.chat.completions.create(
        model=DEEPSEEK_MODEL,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ],
        stream=True,
        reasoning_effort="high",
        extra_body={"thinking": {"type": "enabled"}},
        temperature=temperature,
    )
    full_text = []
    for chunk in response:
        if chunk.choices and chunk.choices[0].delta.content:
            token = chunk.choices[0].delta.content
            full_text.append(token)
            yield token
    return "".join(full_text)
