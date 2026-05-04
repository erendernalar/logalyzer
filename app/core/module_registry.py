import importlib
import pkgutil
import app.modules as _modules_pkg
from app.modules.base_module import BaseModule


class ModuleRegistry:
    """
    Scans app/modules/ at startup and registers every BaseModule subclass found.
    No explicit imports or config changes are needed when adding a new module.
    """

    def __init__(self):
        self._modules: list[type] = []   # ordered list of BaseModule subclasses
        self._instances: dict[str, BaseModule] = {}

    def discover(self):
        """Import every app/modules/*/module.py and register BaseModule subclasses."""
        pkg_path = _modules_pkg.__path__
        pkg_name = _modules_pkg.__name__

        for finder, subpkg_name, ispkg in pkgutil.iter_modules(pkg_path):
            if not ispkg:
                continue
            module_file = f"{pkg_name}.{subpkg_name}.module"
            try:
                mod = importlib.import_module(module_file)
            except ModuleNotFoundError:
                continue
            except Exception as exc:
                print(f"[ModuleRegistry] Failed to import {module_file}: {exc}")
                continue

            for attr_name in dir(mod):
                obj = getattr(mod, attr_name)
                if (isinstance(obj, type)
                        and issubclass(obj, BaseModule)
                        and obj is not BaseModule
                        and obj.MODULE_ID):
                    if obj not in self._modules:
                        self._modules.append(obj)

        # Sort by DISPLAY_NAME for consistent sidebar order
        self._modules.sort(key=lambda c: c.DISPLAY_NAME)

    def get_all(self) -> list:
        return list(self._modules)

    def get_instance(self, module_id: str) -> BaseModule:
        """Return a cached module instance, creating it if needed."""
        if module_id not in self._instances:
            for cls in self._modules:
                if cls.MODULE_ID == module_id:
                    self._instances[module_id] = cls()
                    break
        return self._instances.get(module_id)

    def all_instances(self):
        """Yield instantiated instances for all registered modules."""
        for cls in self._modules:
            yield self.get_instance(cls.MODULE_ID)
