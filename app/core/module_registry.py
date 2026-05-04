import app.modules  # noqa: F401 — side-effect: registers all BaseModule subclasses
from app.modules.base_module import BaseModule


def _all_subclasses(cls):
    for sub in cls.__subclasses__():
        yield sub
        yield from _all_subclasses(sub)


class ModuleRegistry:

    def __init__(self):
        self._modules: list[type] = []
        self._instances: dict[str, BaseModule] = {}

    def discover(self):
        for cls in _all_subclasses(BaseModule):
            if cls.MODULE_ID and cls not in self._modules:
                self._modules.append(cls)
        self._modules.sort(key=lambda c: c.DISPLAY_NAME)

    def get_all(self) -> list:
        return list(self._modules)

    def get_instance(self, module_id: str) -> BaseModule:
        if module_id not in self._instances:
            for cls in self._modules:
                if cls.MODULE_ID == module_id:
                    self._instances[module_id] = cls()
                    break
        return self._instances.get(module_id)

    def all_instances(self):
        for cls in self._modules:
            yield self.get_instance(cls.MODULE_ID)
