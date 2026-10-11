"""بوت الذهب XAU/USD على GitHub Actions - شغّال من افتتاح السوق لإغلاقه
استراتيجية 1: مناطق دعم/مقاومة قوية على M15 (ارتداد، سحب سيولة، كسر، إعادة اختبار)
استراتيجية 2: اندفاع قوي ثم تراجع مع الاتجاه
تأكيد من H1 وH4، تنبيه تسكير عند الانعكاس وأنت بربح، ورسالة واضحة عند الهدف الأول"""
import os
import sys
import json
import math
import time
from datetime import datetime, timezone
import numpy as np
import pandas as pd
import requests


def env(name, default=""):
    v = os.environ.get(name)
    return v if v not in (None, "") else default


def envf(name, default):
    return float(env(name, str(default)))


def envi(name, default):
    return int(float(env(name, str(default))))

