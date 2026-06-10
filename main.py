import asyncio
import base64
import re

import aiohttp

import astrbot.api.message_components as Comp
from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, register


@register(
    "astrbot_plugin_draw",
    "you",
    "极简画图插件。/画图 提示词（可附带图片），走 OpenAI 格式接口。",
    "1.0.0",
    "",
)
class DrawPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config

    # ---------- 配置读取 ----------
    @property
    def base_url(self) -> str:
        u = (self.config.get("base_url", "") or "").strip().rstrip("/")
        # 容错：用户可能把完整路径也填进来了，砍掉末尾的已知接口路径
        for suffix in (
            "/chat/completions",
            "/images/generations",
            "/images/edits",
            "/images",
        ):
            if u.endswith(suffix):
                u = u[: -len(suffix)]
                break
        return u.rstrip("/")

    @property
    def api_key(self) -> str:
        return (self.config.get("api_key", "") or "").strip()

    @property
    def model(self) -> str:
        return (self.config.get("model", "") or "").strip()

    @property
    def mode(self) -> str:
        return (self.config.get("mode", "images") or "images").strip().lower()

    @property
    def size(self) -> str:
        return (self.config.get("size", "1024x1024") or "1024x1024").strip()

    @property
    def admin_only(self) -> bool:
        return bool(self.config.get("admin_only", True))

    @property
    def timeout(self) -> int:
        return int(self.config.get("timeout", 180) or 180)

    @property
    def extra_prompt(self) -> str:
        return self.config.get("extra_prompt", "") or ""

    @property
    def headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    # ---------- 指令 ----------
    @filter.command("画图", alias={"draw", "绘图"})
    async def draw(self, event: AstrMessageEvent):
        """/画图 提示词（可附带图片，支持图生图）"""
        # 权限
        if self.admin_only and not event.is_admin():
            yield event.plain_result("⚠️ 仅管理员可用本指令（可在插件配置里关闭 admin_only）。")
            return

        # 取提示词：去掉指令本身
        prompt = event.message_str or ""
        prompt = re.sub(r"^\s*(画图|draw|绘图)\s*", "", prompt).strip()
        if self.extra_prompt:
            prompt = (prompt + " " + self.extra_prompt).strip()

        # 取附带图片
        input_images = await self._collect_images(event)

        if not prompt and not input_images:
            yield event.plain_result("用法：/画图 你的提示词（可同时发送图片做图生图）")
            return

        # 配置检查
        if not self.base_url or not self.api_key or not self.model:
            yield event.plain_result("❌ 未配置完整：请在插件配置里填 base_url / api_key / model。")
            return

        yield event.plain_result("🎨 正在画…")

        try:
            if self.mode == "chat":
                img_bytes, text = await self._gen_chat(prompt, input_images)
            else:
                img_bytes, text = await self._gen_images(prompt, input_images)
        except Exception as e:
            logger.exception("[draw] 生成失败")
            yield event.plain_result(f"❌ 生成失败：{e}")
            return

        chain = []
        if text:
            chain.append(Comp.Plain(text))
        if img_bytes:
            chain.append(Comp.Image.fromBytes(img_bytes))
            yield event.chain_result(chain)
        else:
            yield event.plain_result(text or "❌ 接口未返回图片。")

    # ---------- 统一请求：失败时带上状态码和原始返回 ----------
    async def _post_json(self, session, url, *, json=None, data=None, headers=None):
        async with session.post(url, json=json, data=data, headers=headers) as resp:
            raw = await resp.text()
            try:
                import json as _json
                return _json.loads(raw)
            except Exception:
                snippet = raw.strip().replace("\n", " ")[:200]
                if not snippet:
                    snippet = "(空响应)"
                raise RuntimeError(
                    f"接口未返回 JSON [HTTP {resp.status}] @ {url}\n"
                    f"原始返回：{snippet}\n"
                    f"→ 多半是 base_url 路径不对、模型名错、或该接口走错了 mode。"
                )

    @filter.command("画图配置")
    async def draw_debug(self, event: AstrMessageEvent):
        """查看当前画图配置与实际请求地址"""
        if self.admin_only and not event.is_admin():
            yield event.plain_result("⚠️ 仅管理员可用。")
            return
        if self.mode == "chat":
            real = f"{self.base_url}/chat/completions"
        else:
            real = f"{self.base_url}/images/generations (文生图) | {self.base_url}/images/edits (图生图)"
        masked = (self.api_key[:4] + "***" + self.api_key[-4:]) if len(self.api_key) > 8 else "(未填或过短)"
        yield event.plain_result(
            "🛠 当前画图配置\n"
            f"mode   : {self.mode}\n"
            f"base_url: {self.base_url or '(未填)'}\n"
            f"model  : {self.model or '(未填)'}\n"
            f"api_key: {masked}\n"
            f"size   : {self.size}\n"
            f"实际请求: {real}"
        )

    # ---------- 收集图片 ----------
    async def _collect_images(self, event: AstrMessageEvent) -> list[bytes]:
        images: list[bytes] = []
        for seg in event.get_messages():
            if isinstance(seg, Comp.Image):
                data = await self._read_image_seg(seg)
                if data:
                    images.append(data)
        return images

    async def _read_image_seg(self, seg: Comp.Image) -> bytes | None:
        # 优先用组件自带方法
        try:
            b64 = await seg.convert_to_base64()
            if b64:
                if "," in b64:
                    b64 = b64.split(",", 1)[1]
                return base64.b64decode(b64)
        except Exception:
            pass
        # 回退：file 字段可能是 url / 本地路径 / base64
        url = getattr(seg, "url", None) or getattr(seg, "file", None)
        if not url:
            return None
        if url.startswith("base64://"):
            return base64.b64decode(url[len("base64://"):])
        if url.startswith("http"):
            async with aiohttp.ClientSession() as s:
                async with s.get(url) as r:
                    return await r.read()
        try:
            with open(url, "rb") as f:
                return f.read()
        except Exception:
            return None

    # ---------- images 接口 ----------
    async def _gen_images(self, prompt: str, input_images: list[bytes]):
        timeout = aiohttp.ClientTimeout(total=self.timeout)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            if input_images:
                payload = await self._images_edit(session, prompt, input_images)
            else:
                # 文生图：/v1/images/generations
                url = f"{self.base_url}/images/generations"
                body = {"model": self.model, "prompt": prompt, "n": 1}
                if self.size:
                    body["size"] = self.size
                payload = await self._post_json(session, url, json=body, headers=self.headers)

        return await self._parse_images_payload(payload)

    async def _images_edit(self, session, prompt: str, input_images: list[bytes]):
        """图生图 /v1/images/edits。
        不同服务对 edits 的 image 字段格式要求不一：
          - xAI/grok 官方：JSON，image={"type":"image_url","url":data_uri}
          - 部分中转：JSON，image=data_uri（扁平字符串）
          - 标准 OpenAI：multipart/form-data
        这里依次尝试，直到拿到 JSON 响应为止。
        """
        url = f"{self.base_url}/images/edits"
        data_uris = [
            f"data:image/png;base64,{base64.b64encode(img).decode()}"
            for img in input_images
        ]
        p = prompt or "edit this image"

        # 候选 JSON body 列表（按可能性排序）
        candidates = []
        if len(data_uris) == 1:
            # 1) xAI 官方对象格式
            candidates.append({
                "model": self.model, "prompt": p,
                "image": {"type": "image_url", "url": data_uris[0]},
            })
            # 2) 扁平字符串格式
            candidates.append({
                "model": self.model, "prompt": p,
                "image": data_uris[0],
            })
            # 3) 数组对象格式
            candidates.append({
                "model": self.model, "prompt": p,
                "images": [{"type": "image_url", "url": data_uris[0]}],
            })
        else:
            candidates.append({
                "model": self.model, "prompt": p,
                "images": [{"type": "image_url", "url": u} for u in data_uris],
            })
            candidates.append({
                "model": self.model, "prompt": p,
                "image": [{"type": "image_url", "url": u} for u in data_uris],
            })

        last_err = None
        for body in candidates:
            try:
                payload = await self._post_json(session, url, json=body, headers=self.headers)
            except Exception as e:
                # 非 JSON 响应（可能要 multipart）—— 停止 JSON 尝试，去回退
                last_err = e
                break
            # 拿到 JSON 了。如果是 image 字段相关的报错，换下一种格式重试
            err_msg = self._extract_error_msg(payload)
            if err_msg and ("image" in err_msg.lower() and "required" in err_msg.lower()):
                last_err = RuntimeError(err_msg)
                continue
            return payload  # 成功，或其它业务错误（交给 parse 报具体错）

        # —— 回退：multipart（标准 OpenAI 风格）——
        try:
            form = aiohttp.FormData()
            form.add_field("model", self.model)
            form.add_field("prompt", p)
            if self.size:
                form.add_field("size", self.size)
            for i, img in enumerate(input_images):
                form.add_field(
                    "image[]" if len(input_images) > 1 else "image",
                    img,
                    filename=f"image_{i}.png",
                    content_type="image/png",
                )
            headers = {"Authorization": f"Bearer {self.api_key}"}
            return await self._post_json(session, url, data=form, headers=headers)
        except Exception as e:
            raise last_err or e

    def _extract_error_msg(self, payload):
        if isinstance(payload, dict) and payload.get("error"):
            err = payload["error"]
            return err.get("message") if isinstance(err, dict) else str(err)
        return None


    async def _parse_images_payload(self, payload: dict):
        if not isinstance(payload, dict):
            raise RuntimeError(f"返回格式异常：{payload}")
        if "error" in payload and payload["error"]:
            err = payload["error"]
            raise RuntimeError(err.get("message", str(err)) if isinstance(err, dict) else str(err))
        data = payload.get("data") or []
        if not data:
            raise RuntimeError(f"无图片数据：{str(payload)[:300]}")
        item = data[0]
        if item.get("b64_json"):
            return base64.b64decode(item["b64_json"]), None
        if item.get("url"):
            async with aiohttp.ClientSession() as s:
                async with s.get(item["url"]) as r:
                    return await r.read(), None
        raise RuntimeError(f"返回项无 url/b64：{item}")

    # ---------- chat 多模态接口 ----------
    async def _gen_chat(self, prompt: str, input_images: list[bytes]):
        url = f"{self.base_url}/chat/completions"
        content = [{"type": "text", "text": prompt or "生成一张图片"}]
        for img in input_images:
            b64 = base64.b64encode(img).decode()
            content.append({
                "type": "image_url",
                "image_url": {"url": f"data:image/png;base64,{b64}"},
            })
        body = {
            "model": self.model,
            "messages": [{"role": "user", "content": content}],
            "stream": False,
        }
        timeout = aiohttp.ClientTimeout(total=self.timeout)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            payload = await self._post_json(session, url, json=body, headers=self.headers)
        return await self._parse_chat_payload(payload)

    async def _parse_chat_payload(self, payload: dict):
        if not isinstance(payload, dict):
            raise RuntimeError(f"返回格式异常：{payload}")
        if payload.get("error"):
            err = payload["error"]
            raise RuntimeError(err.get("message", str(err)) if isinstance(err, dict) else str(err))
        try:
            msg = payload["choices"][0]["message"]
        except Exception:
            raise RuntimeError(f"返回无 choices：{str(payload)[:300]}")

        img_bytes = None
        text_out = None

        # 1) 部分中转把图片放 message.images
        imgs = msg.get("images")
        if isinstance(imgs, list) and imgs:
            first = imgs[0]
            u = first.get("image_url", {}).get("url") if isinstance(first, dict) else first
            img_bytes = await self._url_or_b64_to_bytes(u)

        # 2) content 里找 markdown 图 / data uri / 直链
        content = msg.get("content")
        text = content if isinstance(content, str) else self._content_to_text(content)
        if img_bytes is None and text:
            img_bytes = await self._extract_image_from_text(text)
            # 去掉文本里的图片链接，剩纯文字
            clean = re.sub(r"!\[[^\]]*\]\([^)]+\)", "", text)
            clean = re.sub(r"data:image/[^\s)]+", "", clean)
            clean = clean.strip()
            text_out = clean or None

        return img_bytes, text_out

    def _content_to_text(self, content) -> str:
        if isinstance(content, list):
            parts = []
            for c in content:
                if isinstance(c, dict) and c.get("type") == "text":
                    parts.append(c.get("text", ""))
            return "\n".join(parts)
        return str(content or "")

    async def _extract_image_from_text(self, text: str) -> bytes | None:
        # data uri
        m = re.search(r"data:image/[^;]+;base64,([A-Za-z0-9+/=\n]+)", text)
        if m:
            return base64.b64decode(m.group(1).strip())
        # markdown / 直链
        m = re.search(r"!\[[^\]]*\]\((https?://[^)\s]+)\)", text) or \
            re.search(r"(https?://[^\s)]+\.(?:png|jpg|jpeg|webp|gif)[^\s)]*)", text, re.I)
        if m:
            return await self._url_or_b64_to_bytes(m.group(1))
        return None

    async def _url_or_b64_to_bytes(self, u: str | None) -> bytes | None:
        if not u:
            return None
        if u.startswith("data:image"):
            return base64.b64decode(u.split(",", 1)[1])
        if u.startswith("http"):
            async with aiohttp.ClientSession() as s:
                async with s.get(u) as r:
                    return await r.read()
        return None

    async def terminate(self):
        pass
