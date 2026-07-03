"""
white_salary/adapters/platform/qzone_api.py

QQ空间API封装 — 发说说/发图/回复评论/获取说说/获取评论。

参考v2的qzone.py，完全重写适配我们的异步架构。
Cookie需要：uin(QQ号)、skey、p_skey，配置在config/qzone.ini。

所有API都走QQ空间Web接口（非官方API），需要登录态。
"""

import json
import re
import configparser
from pathlib import Path
from typing import Optional

import aiohttp
from loguru import logger


def _compute_gtk(p_skey: str) -> int:
    """用p_skey计算g_tk令牌。"""
    h = 5381
    for c in p_skey:
        h += (h << 5) + ord(c)
    return h & 0x7FFFFFFF


class QZoneClient:
    """QQ空间客户端（异步）。"""

    # Cookie过期错误码
    LOGIN_REQUIRED_CODES = {-3000, -10001, 1003}
    # -10000需要连续出现才判定过期（系统繁忙和真过期共用这个码）
    BUSY_ERROR_CODE = -10000
    BUSY_THRESHOLD = 5  # 连续5次-10000才判定过期

    def __init__(self, config_path: str = "config/qzone.ini"):
        self._config_path = Path(config_path)
        self._uin = ""
        self._skey = ""
        self._p_skey = ""
        self._gtk = 0
        self._cookie_expired = False
        self._consecutive_busy = 0  # 连续-10000次数
        self._load_config()

    def _load_config(self):
        """从ini文件加载Cookie。"""
        if not self._config_path.exists():
            return
        try:
            cp = configparser.RawConfigParser()
            cp.read(str(self._config_path), encoding="utf-8")
            self._uin = cp.get("qzone", "uin", fallback="")
            self._skey = cp.get("qzone", "skey", fallback="")
            self._p_skey = cp.get("qzone", "p_skey", fallback="")
            if self._p_skey:
                self._gtk = _compute_gtk(self._p_skey)
        except Exception as e:
            logger.warning(f"[QZone] 配置加载失败: {e}")

    def save_config(self, uin: str, skey: str, p_skey: str):
        """保存Cookie到ini文件。"""
        self._config_path.parent.mkdir(parents=True, exist_ok=True)
        cp = configparser.RawConfigParser()
        cp.add_section("qzone")
        cp.set("qzone", "uin", uin)
        cp.set("qzone", "skey", skey)
        cp.set("qzone", "p_skey", p_skey)
        with open(self._config_path, "w", encoding="utf-8") as f:
            cp.write(f)
        self._uin = uin
        self._skey = skey
        self._p_skey = p_skey
        self._gtk = _compute_gtk(p_skey) if p_skey else 0
        self._cookie_expired = False
        self._consecutive_busy = 0
        logger.info("[QZone] Cookie已保存，过期状态已重置")

    @property
    def is_configured(self) -> bool:
        return bool(self._uin and self._skey and self._p_skey)

    @property
    def is_cookie_expired(self) -> bool:
        """Cookie是否已过期（需要重新登录）。"""
        return self._cookie_expired

    @property
    def uin(self) -> str:
        return self._uin

    def _check_login_error(self, result: dict) -> bool:
        """
        检查API返回是否表示Cookie过期。

        Returns:
            True = Cookie过期，需要重新登录
        """
        code = result.get("code", result.get("ret"))
        if code is None:
            return False

        code = int(code) if isinstance(code, str) and code.lstrip('-').isdigit() else code

        # 明确的登录过期码
        if code in self.LOGIN_REQUIRED_CODES:
            self._cookie_expired = True
            logger.warning(f"[QZone] Cookie已过期 (code={code})，请重新登录")
            return True

        # -10000需要连续多次才判定
        if code == self.BUSY_ERROR_CODE:
            self._consecutive_busy += 1
            if self._consecutive_busy >= self.BUSY_THRESHOLD:
                self._cookie_expired = True
                logger.warning(f"[QZone] 连续{self._consecutive_busy}次系统繁忙，判定Cookie过期")
                return True
        else:
            # 其他码正常，重置计数
            self._consecutive_busy = 0

        return False

    def _get_headers(self) -> dict:
        return {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Referer": f"https://user.qzone.qq.com/{self._uin}",
            "Origin": "https://user.qzone.qq.com",
            "Content-Type": "application/x-www-form-urlencoded",
        }

    def _get_cookies(self) -> dict:
        return {
            "uin": f"o{self._uin}",
            "skey": self._skey,
            "p_skey": self._p_skey,
            "p_uin": f"o{self._uin}",
        }

    def _parse_callback(self, text: str) -> dict:
        """解析QQ空间API返回的_Callback(...)格式。"""
        if text.startswith("_Callback("):
            text = text[10:-2]
        try:
            return json.loads(text)
        except Exception:
            # 尝试从文本中提取JSON
            match = re.search(r'\{.*\}', text, re.DOTALL)
            if match:
                return json.loads(match.group())
            return {"code": -1, "message": "解析失败"}

    # ================================================================
    # 发说说
    # ================================================================

    async def post_emotion(self, content: str, pic_info: dict = None) -> dict:
        """
        发表说说（纯文字或带图）。

        Args:
            content: 说说内容
            pic_info: 图片信息（从upload_image返回），None=纯文字

        Returns:
            {"success": True/False, "tid": "...", "error": "..."}
        """
        if not self.is_configured:
            return {"success": False, "error": "未配置QQ空间Cookie"}

        url = "https://user.qzone.qq.com/proxy/domain/taotao.qzone.qq.com/cgi-bin/emotion_cgi_publish_v6"

        data = {
            "syn_tweet_verson": "1",
            "paramstr": "1",
            "who": "1",
            "con": content,
            "feedversion": "1",
            "ver": "1",
            "ugc_right": "1",
            "to_sign": "0",
            "hostuin": self._uin,
            "code_version": "1",
            "format": "json",
            "qzreferrer": f"https://user.qzone.qq.com/{self._uin}",
        }

        # 带图片
        if pic_info and pic_info.get("success"):
            richval = pic_info.get("richval", "")
            pic_bo = pic_info.get("bo", "") or pic_info.get("url", "")
            if richval:
                data["richtype"] = "1"
                data["richval"] = richval
                data["pic_bo"] = pic_bo

        try:
            async with aiohttp.ClientSession(cookies=self._get_cookies()) as session:
                async with session.post(
                    url, params={"g_tk": self._gtk, "uin": self._uin},
                    data=data, headers=self._get_headers(),
                    timeout=aiohttp.ClientTimeout(total=15),
                ) as resp:
                    result = self._parse_callback(await resp.text())
                    if result.get("code") == 0 or result.get("ret") == 0:
                        self._consecutive_busy = 0
                        logger.info(f"[QZone] 说说发布成功: {content[:30]}")
                        return {"success": True, "tid": result.get("tid", "")}
                    else:
                        self._check_login_error(result)
                        error = result.get("message") or result.get("msg") or str(result)
                        logger.warning(f"[QZone] 说说发布失败: {error}")
                        return {"success": False, "error": error}
        except Exception as e:
            logger.warning(f"[QZone] 发说说异常: {e}")
            return {"success": False, "error": str(e)}

    # ================================================================
    # 上传图片
    # ================================================================

    async def upload_image(self, image_data: bytes, filename: str = "image.jpg") -> dict:
        """
        上传图片到QQ空间。

        Returns:
            {"success": True, "richval": "...", "bo": "url", ...}
        """
        if not self.is_configured:
            return {"success": False, "error": "未配置"}

        url = "https://up.qzone.qq.com/cgi-bin/upload/cgi_upload_image"

        form = aiohttp.FormData()
        form.add_field("filename", image_data, filename=filename, content_type="image/jpeg")
        form.add_field("uploadtype", "1")
        form.add_field("albumtype", "7")
        form.add_field("exttype", "0")
        form.add_field("skey", self._skey)
        form.add_field("zzpaneluin", self._uin)
        form.add_field("p_uin", self._uin)
        form.add_field("uin", self._uin)
        form.add_field("p_skey", self._p_skey)
        form.add_field("qzonetoken", "")
        form.add_field("output_type", "json")
        form.add_field("refer", "shuoshuo")

        try:
            async with aiohttp.ClientSession(cookies=self._get_cookies()) as session:
                async with session.post(
                    url, params={"g_tk": self._gtk, "uin": self._uin},
                    data=form,
                    headers={
                        "User-Agent": self._get_headers()["User-Agent"],
                        "Referer": f"https://user.qzone.qq.com/{self._uin}",
                    },
                    timeout=aiohttp.ClientTimeout(total=30),
                ) as resp:
                    text = await resp.text()
                    match = re.search(r'\{.*\}', text, re.DOTALL)
                    if match:
                        result = json.loads(match.group())
                        if result.get("ret") == 0 or "data" in result:
                            img = result.get("data", result)
                            albumid = img.get("albumid", "")
                            lloc = img.get("lloc", "")
                            sloc = img.get("sloc", "")
                            w = img.get("width", 0)
                            h = img.get("height", 0)
                            richval = f",{albumid},{lloc},{sloc},,{w},{h}"
                            logger.info(f"[QZone] 图片上传成功")
                            return {
                                "success": True,
                                "bo": img.get("pre") or img.get("url", ""),
                                "url": img.get("url", ""),
                                "richval": richval,
                            }
                    return {"success": False, "error": "上传返回格式异常"}
        except Exception as e:
            logger.warning(f"[QZone] 图片上传异常: {e}")
            return {"success": False, "error": str(e)}

    # ================================================================
    # 获取说说列表
    # ================================================================

    async def get_feeds(self, count: int = 5, target_uin: str = "") -> list:
        """
        获取说说列表（用h5.qzone.qq.com + GET，不会被限流）。

        Args:
            count: 获取数量
            target_uin: 目标QQ号（空=自己）

        Returns:
            [{"tid": "...", "content": "...", "created_time": 123}, ...]
        """
        if not self.is_configured:
            return []

        uin = target_uin or self._uin
        url = "https://h5.qzone.qq.com/proxy/domain/taotao.qq.com/cgi-bin/emotion_cgi_msglist_v6"
        params = {
            "uin": uin, "num": count, "pos": 0, "ftype": "0", "sort": "0",
            "code_version": "1", "format": "json", "need_private_comment": "1",
            "g_tk": self._gtk,
        }
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Referer": f"https://user.qzone.qq.com/{self._uin}",
        }

        try:
            async with aiohttp.ClientSession(cookies=self._get_cookies()) as session:
                async with session.get(
                    url, params=params, headers=headers,
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as resp:
                    result = self._parse_callback(await resp.text())
                    if result.get("code") == 0:
                        self._consecutive_busy = 0
                        feeds = []
                        for msg in (result.get("msglist") or [])[:count]:
                            feeds.append({
                                "tid": msg.get("tid", ""),
                                "content": msg.get("content", ""),
                                "created_time": msg.get("created_time", 0),
                                "name": msg.get("name", ""),
                                "uin": str(msg.get("uin", "")),
                            })
                        return feeds
                    else:
                        self._check_login_error(result)
                        code = result.get("code")
                        msg = result.get("message") or ""
                        logger.warning(f"[QZone] 获取说说失败: code={code}, msg={msg}")
        except Exception as e:
            logger.warning(f"[QZone] 获取说说异常: {e}")
        return []

    # ================================================================
    # 获取评论
    # ================================================================

    async def get_comments(self, tid: str, owner_uin: str = "") -> list:
        """获取某条说说的评论列表。"""
        if not self.is_configured:
            return []

        uin = owner_uin or self._uin
        url = "https://h5.qzone.qq.com/proxy/domain/taotao.qq.com/cgi-bin/emotion_cgi_msgdetail_v6"

        data = {
            "uin": uin,
            "tid": tid,
            "num": 10,
            "pos": 0,
            "format": "json",
            "qzreferrer": f"https://user.qzone.qq.com/{self._uin}",
        }

        try:
            async with aiohttp.ClientSession(cookies=self._get_cookies()) as session:
                async with session.post(
                    url, params={"g_tk": self._gtk, "uin": self._uin},
                    data=data, headers=self._get_headers(),
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as resp:
                    result = self._parse_callback(await resp.text())
                    if result.get("code") == 0 or result.get("ret") == 0:
                        self._consecutive_busy = 0
                        comments = []
                        for cmt in (result.get("commentlist") or []):
                            # QQ空间评论ID：优先commentid，然后tid（评论序号），然后其他字段
                            cmt_id = (cmt.get("commentid") or cmt.get("id")
                                      or cmt.get("cmt_id") or str(cmt.get("tid", ""))
                                      or str(cmt.get("tid2", "")))
                            comments.append({
                                "commentid": str(cmt_id),
                                "uin": str(cmt.get("uin", "")),
                                "name": cmt.get("name", ""),
                                "content": cmt.get("content", ""),
                                "create_time": cmt.get("create_time", 0),
                                "tid": tid,
                            })
                        return comments
                    else:
                        self._check_login_error(result)
        except Exception as e:
            logger.warning(f"[QZone] 获取评论异常: {e}")
        return []

    # ================================================================
    # 回复评论
    # ================================================================

    async def reply_comment(
        self,
        tid: str,
        content: str,
        commentid: str = "",
        reply_uin: str = "",
        host_uin: str = "",
    ) -> dict:
        """
        发评论或回复评论。

        两种模式：
          1. 一级评论（commentid为空）：在别人/自己的说说下直接评论
          2. 回复评论（commentid不为空）：回复某条已有的评论

        Args:
            tid: 说说ID
            content: 评论/回复内容
            commentid: 要回复的评论ID（空=发一级评论）
            reply_uin: 被回复者QQ号（回复评论时传，让对方收到通知）
            host_uin: 说说发布者QQ号（逛别人空间评论时传，空=自己）
        """
        if not self.is_configured:
            return {"success": False, "error": "未配置"}

        url = "https://user.qzone.qq.com/proxy/domain/taotao.qq.com/cgi-bin/emotion_cgi_re_feeds"

        # 说说归属者（评论别人的说说时用对方QQ号）
        owner = host_uin or self._uin

        # 一级评论和回复评论用不同的feedsType
        is_reply = bool(commentid)

        data = {
            "uin": self._uin,
            "topicId": f"{owner}_{tid}__1",  # 双下划线+1
            "feedsType": "102" if is_reply else "100",  # 回复=102，一级=100
            "inCharset": "utf-8",
            "outCharset": "utf-8",
            "plat": "qzone",
            "source": "ic",
            "hostUin": owner,
            "platformid": "50",
            "format": "fs",  # QQ空间用fs格式
            "ref": "feeds",
            "paramstr": "1",
            "content": content,
        }

        # 回复评论模式（带commentId+commentUin+replyUin）
        if is_reply:
            data["commentId"] = commentid
            data["commentUin"] = reply_uin or owner  # 被回复评论的发布者QQ号
            if reply_uin:
                data["replyUin"] = reply_uin

        try:
            async with aiohttp.ClientSession(cookies=self._get_cookies()) as session:
                async with session.post(
                    url, params={"g_tk": self._gtk},
                    data=data, headers=self._get_headers(),
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as resp:
                    resp_text = await resp.text()
                    # format=fs返回HTML（成功时有<li class=），format=json返回JSON
                    if "<li class=" in resp_text or '"code":0' in resp_text or '"ret":0' in resp_text:
                        self._consecutive_busy = 0
                        logger.info(f"[QZone] 评论回复成功: {content[:30]}")
                        return {"success": True}
                    else:
                        result = self._parse_callback(resp_text)
                        self._check_login_error(result)
                        error = result.get("message") or result.get("msg") or str(result)
                        return {"success": False, "error": error}
        except Exception as e:
            return {"success": False, "error": str(e)}

    # ================================================================
    # 删除说说
    # ================================================================

    async def delete_emotion(self, tid: str) -> dict:
        """删除自己的说说。"""
        if not self.is_configured:
            return {"success": False, "error": "未配置"}
        if not tid:
            return {"success": False, "error": "缺少说说ID"}

        url = "https://h5.qzone.qq.com/proxy/domain/taotao.qq.com/cgi-bin/emotion_cgi_delete_v6"

        data = {
            "uin": self._uin,
            "hostuin": self._uin,
            "tid": tid,
            "t1_source": "1",
            "code_version": "1",
            "format": "json",
            "qzreferrer": f"https://user.qzone.qq.com/{self._uin}",
        }

        try:
            async with aiohttp.ClientSession(cookies=self._get_cookies()) as session:
                async with session.post(
                    url, params={"g_tk": self._gtk},
                    data=data, headers=self._get_headers(),
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as resp:
                    result = self._parse_callback(await resp.text())
                    if result.get("code") == 0 or result.get("ret") == 0:
                        self._consecutive_busy = 0
                        logger.info(f"[QZone] 说说已删除: tid={tid}")
                        return {"success": True}
                    else:
                        self._check_login_error(result)
                        error = result.get("message") or result.get("msg") or str(result)
                        logger.warning(f"[QZone] 删除说说失败: {error}")
                        return {"success": False, "error": error}
        except Exception as e:
            logger.warning(f"[QZone] 删除说说异常: {e}")
            return {"success": False, "error": str(e)}

    # ================================================================
    # 删除评论
    # ================================================================

    async def delete_comment(self, tid: str, commentid: str, host_uin: str = "") -> dict:
        """删除评论（只能删自己发的评论，用删除说说的API+commentId参数）。"""
        if not self.is_configured:
            return {"success": False, "error": "未配置"}
        if not tid or not commentid:
            return {"success": False, "error": "缺少说说ID或评论ID"}

        owner = host_uin or self._uin
        # 跟删除说说用同一个API，加commentId参数就是删评论
        url = "https://h5.qzone.qq.com/proxy/domain/taotao.qq.com/cgi-bin/emotion_cgi_delete_v6"

        data = {
            "uin": self._uin,
            "hostuin": owner,
            "tid": tid,
            "commentId": commentid,
            "topicId": f"{owner}_{tid}__1",
            "feedsType": "102",
            "platformid": "50",
            "format": "fs",
            "paramstr": "1",
            "qzreferrer": f"https://user.qzone.qq.com/{self._uin}",
        }

        try:
            async with aiohttp.ClientSession(cookies=self._get_cookies()) as session:
                async with session.post(
                    url, params={"g_tk": self._gtk},
                    data=data, headers=self._get_headers(),
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as resp:
                    resp_text = await resp.text()
                    if '"code":0' in resp_text or '"ret":0' in resp_text:
                        self._consecutive_busy = 0
                        logger.info(f"[QZone] 评论已删除: commentid={commentid}")
                        return {"success": True}
                    else:
                        result = self._parse_callback(resp_text)
                        self._check_login_error(result)
                        error = result.get("message") or result.get("msg") or str(result)
                        logger.warning(f"[QZone] 删除评论失败: {error}")
                        return {"success": False, "error": error}
        except Exception as e:
            logger.warning(f"[QZone] 删除评论异常: {e}")
            return {"success": False, "error": str(e)}

    # ================================================================
    # 点赞
    # ================================================================

    async def like_emotion(self, tid: str, host_uin: str = "") -> dict:
        """给说说点赞。"""
        if not self.is_configured:
            return {"success": False, "error": "未配置"}
        if not tid:
            return {"success": False, "error": "缺少说说ID"}

        owner = host_uin or self._uin
        url = "https://user.qzone.qq.com/proxy/domain/w.qzone.qq.com/cgi-bin/likes/internal_dolike_app"

        data = {
            "qzreferrer": f"https://user.qzone.qq.com/{owner}",
            "opuin": self._uin,
            "unikey": f"http://user.qzone.qq.com/{owner}/mood/{tid}",
            "curkey": f"http://user.qzone.qq.com/{owner}/mood/{tid}",
            "from": "1",
            "appid": "311",
            "typeid": "0",
            "abstime": "0",
            "fid": tid,
            "active": "0",
            "fupdate": "1",
            "format": "json",
        }

        try:
            async with aiohttp.ClientSession(cookies=self._get_cookies()) as session:
                async with session.post(
                    url, params={"g_tk": self._gtk},
                    data=data, headers=self._get_headers(),
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as resp:
                    result = self._parse_callback(await resp.text())
                    if result.get("code") == 0 or result.get("ret") == 0:
                        self._consecutive_busy = 0
                        logger.info(f"[QZone] 已点赞: tid={tid}")
                        return {"success": True}
                    else:
                        self._check_login_error(result)
                        error = result.get("message") or result.get("msg") or str(result)
                        logger.warning(f"[QZone] 点赞失败: {error}")
                        return {"success": False, "error": error}
        except Exception as e:
            logger.warning(f"[QZone] 点赞异常: {e}")
            return {"success": False, "error": str(e)}

    # ================================================================
    # 与我相关（通知中心：谁@了我/谁评论了我/谁回复了我）
    # ================================================================

    async def get_notifications(self, count: int = 10) -> list[dict]:
        """
        获取QQ空间"与我相关"通知列表。

        能检测到：别人@白、评论白的说说、回复白的评论——不管在哪条说说下。

        Returns:
            [{"uin": "123", "nick": "小明", "action": "评论", "tid": "xxx",
              "feed_uin": "456"}, ...]
        """
        if not self.is_configured:
            return []

        url = (
            f"https://user.qzone.qq.com/proxy/domain/ic2.qzone.qq.com"
            f"/cgi-bin/feeds/feeds2_html_pav_all"
            f"?uin={self._uin}&begin_time=0&end_time=0"
            f"&getappnotification=1&getnotifi=1&has_get_key=0"
            f"&offset=0&set=0&count={count}&useutf8=1&outputhtmlfeed=1"
            f"&scope=1&g_tk={self._gtk}"
        )

        try:
            async with aiohttp.ClientSession(cookies=self._get_cookies()) as session:
                async with session.get(
                    url,
                    headers={
                        "User-Agent": self._get_headers()["User-Agent"],
                        "Referer": f"https://user.qzone.qq.com/{self._uin}/infocenter",
                    },
                    timeout=aiohttp.ClientTimeout(total=15),
                ) as resp:
                    text = await resp.text()
                    if resp.status != 200 or len(text) < 100:
                        return []

                    # 解析通知块（每条通知以 uin:'xxx',fold 开始）
                    import re
                    blocks = re.split(r"(?=uin:'\d+',fold)", text)
                    notifications = []

                    for block in blocks[1:]:  # 第一个是头部，跳过
                        uin_m = re.search(r"uin:'(\d+)'", block)
                        nick_m = re.search(r"nickname:'([^']*?)'", block)
                        # 提取说说发布者uin和tid
                        feed_uin_m = re.search(r"ouin:'(\d+)'", block) or uin_m
                        tid_m = re.search(r"key:'[^']*_(\w{20,})'", block)

                        if not uin_m:
                            continue

                        # 操作类型：oprType字段（0=评论/回复/@，其他待确认）
                        opr_m = re.search(r"oprType:'(\d+)'", block)
                        opr_type = opr_m.group(1) if opr_m else "-1"

                        action = "评论"  # scope=1（与我相关）默认都是互动类
                        if opr_type == "0":
                            action = "评论"  # 评论/回复/@
                        elif opr_type == "1":
                            action = "点赞"
                        elif opr_type == "2":
                            action = "转发"

                        notifications.append({
                            "uin": uin_m.group(1),
                            "nick": nick_m.group(1) if nick_m else "",
                            "action": action,
                            "tid": tid_m.group(1) if tid_m else "",
                            "feed_uin": feed_uin_m.group(1) if feed_uin_m else "",
                        })

                    return notifications

        except Exception as e:
            logger.debug(f"[QZone] 获取通知异常: {e}")
            return []


# 全局单例
_client: Optional[QZoneClient] = None


def get_client() -> QZoneClient:
    """获取QZone客户端单例。"""
    global _client
    if _client is None:
        _client = QZoneClient()
    return _client
