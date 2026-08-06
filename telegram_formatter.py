import re
import html
from typing import List, Tuple

def fix_unclosed_tags(html_str: str) -> str:
    """
    Ensures that basic Telegram HTML tags (b, i, u, s, code, pre, blockquote, tg-spoiler) 
    in html_str are balanced and properly closed.
    """
    tag_pattern = re.compile(r'</?([a-zA-Z0-9\-]+)[^>]*>')
    stack = []
    
    for match in tag_pattern.finditer(html_str):
        full_tag = match.group(0)
        tag_name = match.group(1).lower()
        
        if full_tag.startswith('</'):
            if stack and stack[-1] == tag_name:
                stack.pop()
            elif tag_name in stack:
                while stack and stack[-1] != tag_name:
                    stack.pop()
                if stack:
                    stack.pop()
        else:
            if not full_tag.endswith('/>'):
                stack.append(tag_name)
                
    for tag in reversed(stack):
        html_str += f'</{tag}>'
        
    return html_str


def md_to_telegram_html(text: str, is_streaming: bool = False) -> str:
    """
    Converts standard Markdown text into Telegram-compliant HTML.
    Supports:
    - Code blocks (```lang ... ```) -> <pre><code class="language-lang">...</code></pre>
    - Inline code (`...`) -> <code>...</code>
    - Blockquotes (> ...) -> <blockquote>...</blockquote>
    - Spoilers (||...||) -> <tg-spoiler>...</tg-spoiler>
    - Headings (#, ##, ###) -> formatted <b>...</b> with emojis
    - Bold (**...** or __...__) -> <b>...</b>
    - Italic (*...* or _..._) -> <i>...</i>
    - Strikethrough (~~...~~) -> <s>...</s>
    - Links ([text](url)) -> <a href="url">text</a>
    - Bullet points (- or *) -> •
    - Horizontal rules (---) -> ───────────────
    - Markdown tables -> <pre>table</pre>
    - Auto HTML-escaping for raw <, >, & outside tags
    - Auto-repair for unclosed tags (especially during streaming)
    """
    if not text:
        return ""

    # If streaming or unclosed code block detected, handle backticks
    backtick_count = len(re.findall(r'```', text))
    if backtick_count % 2 != 0:
        text += "\n```"

    placeholders: List[Tuple[str, str]] = []
    
    def add_placeholder(html_content: str) -> str:
        key = f"XPHKEY{len(placeholders)}XPHKEY"
        placeholders.append((key, html_content))
        return key

    # 1. Extract Fenced Code Blocks: ```lang\ncode\n```
    def replace_code_block(match: re.Match) -> str:
        lang = match.group(1).strip() if match.group(1) else ""
        code_content = match.group(2)
        escaped_code = html.escape(code_content.rstrip())
        if lang:
            tag = f'<pre><code class="language-{html.escape(lang)}">{escaped_code}</code></pre>'
        else:
            tag = f'<pre><code>{escaped_code}</code></pre>'
        return add_placeholder(tag)

    text = re.sub(r'```([a-zA-Z0-9_\-\+\#]*)\n?(.*?)```', replace_code_block, text, flags=re.DOTALL)

    # 2. Extract Inline Code: `code`
    def replace_inline_code(match: re.Match) -> str:
        code_content = match.group(1)
        escaped_code = html.escape(code_content)
        tag = f'<code>{escaped_code}</code>'
        return add_placeholder(tag)

    text = re.sub(r'`([^`\n]+)`', replace_inline_code, text)

    # 3. Extract Spoilers: ||text||
    def replace_spoiler(match: re.Match) -> str:
        content = match.group(1)
        escaped_content = html.escape(content)
        tag = f'<tg-spoiler>{escaped_content}</tg-spoiler>'
        return add_placeholder(tag)

    text = re.sub(r'\|\|(.*?)\|\|', replace_spoiler, text, flags=re.DOTALL)

    # 4. Extract Markdown Tables
    def replace_table(match: re.Match) -> str:
        table_str = match.group(0)
        escaped_table = html.escape(table_str)
        tag = f'<pre>{escaped_table}</pre>'
        return add_placeholder(tag)

    table_pattern = r'(?:(?:\|[^\n]+\|\n)+)'
    text = re.sub(table_pattern, replace_table, text)

    # 5. Safe HTML escape the remaining text
    text = html.escape(text)

    # 6. Convert Blockquotes (> quote)
    def replace_blockquote(match: re.Match) -> str:
        block_text = match.group(0)
        cleaned_lines = []
        for line in block_text.splitlines():
            cleaned = re.sub(r'^(?:&gt;|>)[ \t]?', '', line)
            cleaned_lines.append(cleaned)
        quote_content = "\n".join(cleaned_lines)
        return add_placeholder(f'<blockquote>{quote_content}</blockquote>')

    text = re.sub(r'^(?:&gt;|>)[ \t]?.*(?:\n(?:&gt;|>)[ \t]?.*)*', replace_blockquote, text, flags=re.MULTILINE)

    # 7. Convert Headings (#, ##, ###)
    text = re.sub(r'^[ \t]*# (.*?)$', r'<b>📌 \1</b>\n', text, flags=re.MULTILINE)
    text = re.sub(r'^[ \t]*## (.*?)$', r'<b>🔹 \1</b>\n', text, flags=re.MULTILINE)
    text = re.sub(r'^[ \t]*### (.*?)$', r'<b>• \1</b>\n', text, flags=re.MULTILINE)
    text = re.sub(r'^[ \t]*####+ (.*?)$', r'<b>\1</b>\n', text, flags=re.MULTILINE)

    # 8. Convert Horizontal Rules (---, ***, ___)
    text = re.sub(r'^[ \t]*[-*_]{3,}[ \t]*$', '───────────────', text, flags=re.MULTILINE)

    # 9. Convert Bold: **text** or __text__
    text = re.sub(r'\*\*(.*?)\*\*', r'<b>\1</b>', text, flags=re.DOTALL)
    text = re.sub(r'__(.*?)__', r'<b>\1</b>', text, flags=re.DOTALL)

    # 10. Convert Italic: *text* or _text_
    text = re.sub(r'(?<!\w)\*(?!\s)(.*?)(?<!\s)\*(?!\w)', r'<i>\1</i>', text, flags=re.DOTALL)
    text = re.sub(r'(?<!\w)_(?!\s)(.*?)(?<!\s)_(?!\w)', r'<i>\1</i>', text, flags=re.DOTALL)

    # 11. Convert Strikethrough: ~~text~~
    text = re.sub(r'~~(.*?)~~', r'<s>\1</s>', text, flags=re.DOTALL)

    # 12. Convert Links: [title](url)
    def replace_link(match: re.Match) -> str:
        title = match.group(1)
        url = match.group(2)
        url = html.unescape(url)
        if url.startswith("http://") or url.startswith("https://") or url.startswith("tg://"):
            return f'<a href="{html.escape(url)}">{title}</a>'
        elif url.startswith("file://"):
            clean_path = url.replace("file://", "")
            return f'{title} (<code>{html.escape(clean_path)}</code>)'
        return f'{title} ({html.escape(url)})'

    text = re.sub(r'\[(.*?)\]\((.*?)\)', replace_link, text)

    # 13. Normalize List Bullets
    text = re.sub(r'^[ \t]*[\*\-\+][ \t]+', '• ', text, flags=re.MULTILINE)

    # 14. Restore Placeholders
    for key, html_val in placeholders:
        text = text.replace(key, html_val)

    # 15. Fix any unclosed tags
    text = fix_unclosed_tags(text)

    return text.strip()


def split_telegram_html(html_text: str, max_length: int = 4000) -> List[str]:
    """
    Splits long HTML text into chunks under max_length without breaking tags.
    Appends closing tags to chunks and re-opens them in subsequent chunks.
    """
    if len(html_text) <= max_length:
        return [html_text]

    chunks = []
    lines = html_text.split('\n')
    current_lines = []
    current_len = 0

    for line in lines:
        line_len = len(line) + 1
        if current_len + line_len > max_length:
            if current_lines:
                chunk_str = '\n'.join(current_lines)
                chunk_fixed = fix_unclosed_tags(chunk_str)
                chunks.append(chunk_fixed)
                current_lines = []
                current_len = 0

            if line_len > max_length:
                for i in range(0, len(line), max_length):
                    sub_chunk = line[i:i+max_length]
                    chunks.append(fix_unclosed_tags(sub_chunk))
            else:
                current_lines.append(line)
                current_len = line_len
        else:
            current_lines.append(line)
            current_len += line_len

    if current_lines:
        chunk_str = '\n'.join(current_lines)
        chunks.append(fix_unclosed_tags(chunk_str))

    return chunks

