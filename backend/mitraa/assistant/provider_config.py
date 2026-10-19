"""Local provider setup. Never return or log the API key."""
import os
from pathlib import Path
from tempfile import NamedTemporaryFile
from urllib.parse import urlsplit


def status():
    from mitraa.config import get_settings
    settings = get_settings()
    missing = [name for name, value in [('API key', settings.nebius_api_key),
        ('endpoint', settings.nebius_base_url), ('model', settings.nemotron_model)] if not value]
    return {'configured': not missing, 'missing': missing, 'base_url': settings.nebius_base_url,
            'model': settings.nemotron_model, 'has_key': bool(settings.nebius_api_key),
            'environment_override': any(os.environ.get(key) for key in
                ('NEBIUS_API_KEY', 'NEBIUS_BASE_URL', 'NEMOTRON_MODEL'))}


def save(base_url: str, model: str, api_key: str = ''):
    from dotenv import dotenv_values, set_key
    from mitraa.config import BACKEND_DIR, get_settings
    base_url = base_url.strip(); model = model.strip(); api_key = api_key.strip()
    try:
        parsed = urlsplit(base_url)
        valid_host = bool(parsed.hostname)
        parsed.port  # reject malformed ports
    except ValueError:
        raise ValueError('Enter a valid provider endpoint.') from None
    if (not valid_host or parsed.username or parsed.password or parsed.query or parsed.fragment
            or (parsed.scheme != 'https' and not
                (parsed.scheme == 'http' and parsed.hostname in {'localhost', '127.0.0.1', '::1'}))):
        raise ValueError('Use an HTTPS endpoint, or HTTP on localhost. Do not include credentials or query parameters.')
    if not model or len(model) > 250 or '\n' in model or '\r' in model:
        raise ValueError('Enter the provider model ID.')
    if '\n' in api_key or '\r' in api_key:
        raise ValueError('The API key must be a single line.')
    path = BACKEND_DIR / '.env'
    values = dotenv_values(path) if path.exists() else {}
    if not (api_key or values.get('NEBIUS_API_KEY') or os.environ.get('NEBIUS_API_KEY')):
        raise ValueError('Enter your API key. Responsibilities and reminders work without it.')
    original = path.read_text(encoding='utf-8') if path.exists() else ''
    temporary = None
    try:
        with NamedTemporaryFile(mode='w', encoding='utf-8', dir=BACKEND_DIR,
                                prefix='.mitraa-provider-', suffix='.tmp', delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(original)
        set_key(str(temporary), 'NEBIUS_BASE_URL', base_url)
        set_key(str(temporary), 'NEMOTRON_MODEL', model)
        if api_key:
            set_key(str(temporary), 'NEBIUS_API_KEY', api_key)
        os.replace(temporary, path)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()
    get_settings.cache_clear()
    return status()
