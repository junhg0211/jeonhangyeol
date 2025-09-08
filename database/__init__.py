"""Database facade split by domain modules.

This package re-exports functions from the legacy db.py for easier
discoverability and future migration. Cogs can `import database as db`
and keep using the same function names.
"""

from .core import *  # noqa: F401,F403
from .economy import *  # noqa: F401,F403
from .inventory import *  # noqa: F401,F403
from .auctions import *  # noqa: F401,F403
from .activity import *  # noqa: F401,F403
from .patents import *  # noqa: F401,F403
from .trading import *  # noqa: F401,F403
from .attendance import *  # noqa: F401,F403
from .auto_transfer import *  # noqa: F401,F403
from .announcements import *  # noqa: F401,F403
from .teams import *  # noqa: F401,F403

