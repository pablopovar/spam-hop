from importlib import import_module


def load_processor(spec: str):
    module_name, separator, object_name = spec.partition(":")

    if not separator:
        object_name = "Processor"

    module = import_module(module_name)
    processor_class = getattr(module, object_name)

    return processor_class()
