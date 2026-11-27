"""支付宝网站登录路由配置。"""

from api.external_login import website_login_router
from core import alipay, config

router = website_login_router(
    provider="alipay",
    label="支付宝",
    adapter=alipay,
    app_id=lambda: config.ALIPAY_APP_ID,
    redirect_uri=lambda: config.ALIPAY_REDIRECT_URI,
    code_param="auth_code",
    callback_app_param="app_id",
    allow_callback_probe=True,
)
