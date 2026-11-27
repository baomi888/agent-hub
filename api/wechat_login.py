"""微信网站登录路由配置。"""

from api.external_login import website_login_router
from core import config, wechat

router = website_login_router(
    provider="wechat",
    label="微信",
    adapter=wechat,
    app_id=lambda: config.WECHAT_APP_ID,
    redirect_uri=lambda: config.WECHAT_REDIRECT_URI,
    code_param="code",
)
