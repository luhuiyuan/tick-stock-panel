# coding=utf-8

import logging
import os

DEBUG = os.getenv("TDX2_DEBUG", "")
# DEBUG = True

if DEBUG:
    LOGLEVEL = logging.DEBUG
else:
    LOGLEVEL = logging.INFO

log = logging.getLogger("PYTDX2")

log.setLevel(LOGLEVEL)
if not log.handlers:
    ch = logging.StreamHandler()
    ch.setLevel(LOGLEVEL)
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    ch.setFormatter(formatter)
    log.addHandler(ch)
