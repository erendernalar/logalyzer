from PyQt5.QtWidgets import QStackedWidget
from app.ui.welcome_widget import WelcomeWidget
from app.ui.loading_widget import LoadingWidget


class ContentArea(QStackedWidget):
    """
    Central content area.
    Index 0 — WelcomeWidget (no log loaded)
    Index 1 — LoadingWidget (parsing in progress)
    Index 2+ — module widgets (added lazily, cached by MODULE_ID)
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._welcome = WelcomeWidget()
        self._loading = LoadingWidget()
        self.addWidget(self._welcome)   # index 0
        self.addWidget(self._loading)   # index 1
        self._id_to_index: dict = {}

    @property
    def welcome(self) -> WelcomeWidget:
        return self._welcome

    @property
    def loading(self) -> LoadingWidget:
        return self._loading

    def show_welcome(self):
        self.setCurrentIndex(0)

    def show_loading(self, file_name: str):
        self._loading.start(file_name)
        self.setCurrentIndex(1)

    def show_module(self, module_id: str, module_instance, log_data=None):
        first_build = module_id not in self._id_to_index
        if first_build:
            widget = module_instance.build_widget()
            idx = self.addWidget(widget)
            self._id_to_index[module_id] = idx

        self.setCurrentIndex(self._id_to_index[module_id])

        # Load data AFTER the widget is visible so pyqtgraph plots are fully initialised
        if first_build and log_data is not None:
            module_instance.load_data(log_data)

    def reload_all(self, log_data, registry):
        for module_id in self._id_to_index:
            inst = registry.get_instance(module_id)
            if inst:
                inst.load_data(log_data)

    def clear_all(self, registry):
        for module_id in self._id_to_index:
            inst = registry.get_instance(module_id)
            if inst:
                inst.clear()
        self.show_welcome()
