"""Mark the main executable built by the workstation freeze."""

import sys


sys._workbox_distribution_role = 'workstation'
