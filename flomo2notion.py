import os
import random
import time

import html2text
from markdownify import markdownify

from flomo.flomo_api import FlomoApi
from notionify import notion_utils
from notionify.md2notion import Md2NotionUploader
from notionify.notion_cover_list import cover
from notionify.notion_helper import NotionHelper
from utils import truncate_string, is_within_n_days


def _parse_bool_env(name: str, default: bool = False) -> bool:
    """安全解析布尔环境变量，例如 'true'/'1'/'yes' → True。"""
    val = os.getenv(name)
    if val is None:
        return default
    return str(val).strip().lower() in {"1", "true", "yes", "y", "on"}

def _parse_int_env(name: str, default: int) -> int:
    """安全解析整型环境变量。"""
    val = os.getenv(name)
    if val is None or str(val).strip() == "":
        return default
    try:
        return int(val)
    except Exception:
        return default

def _safe_md_and_text(memo: dict):
    """
    把 flomo 的 content 安全地转成 markdown 和纯文本。
    - 若 content 为 None（例如只有图片），则把图片 URL 拼接成文本；若没有图片则返回空串。
    - 避免 markdownify/html2text 在 None 上崩溃。
    """
    content_html = memo.get("content")
    if not content_html:
        files = memo.get("files") or []
        img_urls = [
            f.get("url")
            for f in files
            if isinstance(f, dict) and f.get("type") == "image" and f.get("url")
        ]
        content_md = "\n".join(img_urls) if img_urls else ""
        content_text = content_md
    else:
        content_md = markdownify(content_html)
        content_text = html2text.html2text(content_html)
    return content_md, content_text


class Flomo2Notion:
    def __init__(self):
        self.flomo_api = FlomoApi()
        self.notion_helper = NotionHelper()
        self.uploader = Md2NotionUploader()

    def insert_memo(self, memo):
        print("insert_memo:", memo)
        # 安全转换内容
        content_md, content_text = _safe_md_and_text(memo)

        parent = {"database_id": self.notion_helper.page_id, "type": "database_id"}

        properties = {
            "标题": notion_utils.get_title(
                truncate_string(content_text)
            ),
            "标签": notion_utils.get_multi_select(
                memo.get("tags") or []
            ),
            "是否置顶": notion_utils.get_select("否" if memo.get("pin", 0) == 0 else "是"),
            # slug是文章唯一标识
            "slug": notion_utils.get_rich_text(memo.get("slug", "")),
            "创建时间": notion_utils.get_date(memo.get("created_at", "")),
            "更新时间": notion_utils.get_date(memo.get("updated_at", "")),
            "来源": notion_utils.get_select(memo.get("source") or ""),
            "链接数量": notion_utils.get_number(memo.get("linked_count", 0)),
        }

        random_cover = random.choice(cover)
        print(f"Random element: {random_cover}")

        page = self.notion_helper.client.pages.create(
            parent=parent,
            icon=notion_utils.get_icon("https://www.notion.so/icons/target_red.svg"),
            cover=notion_utils.get_icon(random_cover),
            properties=properties,
        )

        # 在page里面添加content（Markdown）
        self.uploader.uploadSingleFileContent(self.notion_helper.client, content_md, page["id"])

    def update_memo(self, memo, page_id):
        print("update_memo:", memo)

        # 安全转换内容
        content_md, content_text = _safe_md_and_text(memo)

        # 只更新需要的属性
        properties = {
            "标题": notion_utils.get_title(
                truncate_string(content_text)
            ),
            "更新时间": notion_utils.get_date(memo.get("updated_at", "")),
            "链接数量": notion_utils.get_number(memo.get("linked_count", 0)),
            "标签": notion_utils.get_multi_select(memo.get("tags") or []),
            "是否置顶": notion_utils.get_select("否" if memo.get("pin", 0) == 0 else "是"),
        }
        page = self.notion_helper.client.pages.update(page_id=page_id, properties=properties)

        # 先清空 page 的内容，再重新写入 Markdown
        self.notion_helper.clear_page_content(page["id"])
        self.uploader.uploadSingleFileContent(self.notion_helper.client, content_md, page["id"])

    # 具体步骤：
    # 1. 调用flomo web端的api从flomo获取数据
    # 2. 轮询flomo的列表数据，调用notion api将数据同步写入到database中的page
    def sync_to_notion(self):
        # 1) 从 flomo 获取数据
        authorization = os.getenv("FLOMO_TOKEN")
        memo_list = []
        latest_updated_at = "0"

        while True:
            new_memo_list = self.flomo_api.get_memo_list(authorization, latest_updated_at)
            if not new_memo_list:
                break
            memo_list.extend(new_memo_list)
            latest_updated_at = str(
                int(time.mktime(time.strptime(new_memo_list[-1]["updated_at"], "%Y-%m-%d %H:%M:%S")))
            )

        # 2) 取 Notion 里已有记录（用 slug 去重/更新）
        notion_memo_list = self.notion_helper.query_all(self.notion_helper.page_id)
        slug_map = {}
        for notion_memo in notion_memo_list:
            key = notion_utils.get_rich_text_from_result(notion_memo, "slug")
            if key:
                slug_map[key] = notion_memo.get("id")

        # 3) 写入/更新
        full_update = _parse_bool_env("FULL_UPDATE", False)
        interval_day = _parse_int_env("UPDATE_INTERVAL_DAY", 7)

        for memo in memo_list:
            slug = memo.get("slug")
            if slug in slug_map:
                if not full_update and not is_within_n_days(memo.get("updated_at", ""), interval_day):
                    print("is_within_n_days slug:", slug)
                    continue
                page_id = slug_map[slug]
                self.update_memo(memo, page_id)
            else:
                self.insert_memo(memo)


if __name__ == "__main__":
    # flomo同步到notion入口
    flomo2notion = Flomo2Notion()
    flomo2notion.sync_to_notion()

