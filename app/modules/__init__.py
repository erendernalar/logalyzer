# These explicit imports let PyInstaller statically bundle every module.
# Add a new line here when creating a new module folder.
from app.modules.flight_review_3d import module as _m1      # noqa: F401
from app.modules.log_inspector import module as _m2          # noqa: F401
from app.modules.max_range_analyzer import module as _m3     # noqa: F401
from app.modules.total_flight_time import module as _m4      # noqa: F401
from app.modules.transition_analyzer import module as _m5    # noqa: F401
from app.modules.vibration_analyzer import module as _m6     # noqa: F401
