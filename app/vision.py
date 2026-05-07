import json, requests

VISION_PROMPT = """You are cataloging a private local image archive.
Look at this image and produce a searchable database record.
Do not invent details. If uncertain, say unknown.
Return JSON only with these fields:
short_caption, detailed_description, image_type, category, tags, objects, visible_text.
Pay special attention to stage/theater production, audio/video equipment, projectors, lighting gear, cameras, radio equipment, antennas, network gear, documents, screenshots, logos, flyers, Berkeley/UC Berkeley/Campanile imagery, union or IATSE-related graphics."""

def classify_with_local_model(analysis_path: str, cfg: dict) -> dict:
    if not cfg.get('enabled'):
        return {
            "short_caption": "Vision disabled; thumbnail and metadata indexed only.",
            "detailed_description": "Enable vision.enabled in config.yaml and point base_url/model at a local vision model.",
            "image_type": "unknown",
            "category": "unreviewed",
            "tags": "",
            "objects": "",
            "visible_text": ""
        }
    # Minimal OpenAI-compatible vision call. Many local servers accept image_url data URLs.
    import base64, mimetypes
    with open(analysis_path, 'rb') as f:
        b64 = base64.b64encode(f.read()).decode('ascii')
    mime = mimetypes.guess_type(analysis_path)[0] or 'image/jpeg'
    payload = {
        "model": cfg.get('model'),
        "messages": [{"role": "user", "content": [
            {"type": "text", "text": VISION_PROMPT},
            {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}}
        ]}],
        "temperature": 0.1,
        "max_tokens": 700
    }
    headers = {"Authorization": f"Bearer {cfg.get('api_key','not-needed')}"}
    r = requests.post(cfg.get('base_url').rstrip('/') + '/chat/completions', json=payload, headers=headers, timeout=int(cfg.get('timeout_seconds',180)))
    r.raise_for_status()
    content = r.json()['choices'][0]['message']['content'].strip()
    if content.startswith('```'):
        content = content.strip('`')
        if content.lower().startswith('json'):
            content = content[4:].strip()
    data = json.loads(content)
    for k in ['short_caption','detailed_description','image_type','category','tags','objects','visible_text']:
        v = data.get(k, '')
        if isinstance(v, list):
            v = ', '.join(map(str, v))
        data[k] = str(v)
    return data
