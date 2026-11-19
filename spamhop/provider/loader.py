from importlib import import_module


ALIASES = {
    "ollama": "spamhop.provider.ollama:Provider",
}


def load_provider(spec: str, **kwargs):
    target = ALIASES.get(spec, spec)
    module_name, separator, object_name = target.partition(":")

    if not separator:
        object_name = "Provider"

    module = import_module(module_name)
    provider_class = getattr(module, object_name)
    return provider_class(**kwargs)
