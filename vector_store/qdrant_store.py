from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any
from dotenv import load_dotenv

# لود تضمینی متغیرهای .env در سطح ماژول وکتور استور
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

from qdrant_client import QdrantClient
from qdrant_client.models import Distance, VectorParams

from core.config import settings

COLLECTION_NAME = "motor_threads"
DEFAULT_QDRANT_PATH = Path("data/qdrant_db")

_GLOBAL_STORE: QdrantVectorStore | None = None


def get_qdrant_store(
    url: str | None = None,
    api_key: str | None = None,
    db_path: Path | str | None = None,
    hf_token: str | None = None,
    collection_name: str | None = None,
) -> QdrantVectorStore:
    """دریافت نمونه یکتا (Singleton) از اتصال Qdrant (محلی یا کلود)."""
    global _GLOBAL_STORE
    if _GLOBAL_STORE is None:
        _GLOBAL_STORE = QdrantVectorStore(
            url=url or os.getenv("QDRANT_URL"),
            api_key=api_key or os.getenv("QDRANT_API_KEY"),
            db_path=db_path or DEFAULT_QDRANT_PATH,
            hf_token=hf_token or os.getenv("HF_TOKEN"),
            collection_name=collection_name or COLLECTION_NAME,
        )
    return _GLOBAL_STORE



class QdrantVectorStore:

    """
    مدیریت اتصال به پایگاه برداری Qdrant در حالت سبک Embedded یا Remote (کلود یا سرور مجزا).
    در حالت Remote، مصرف رم محلی پایتون نزدیک به صفر (زیر 80 مگابایت) خواهد بود.
    """

    def __init__(
        self,
        url: str | None = None,
        api_key: str | None = None,
        db_path: Path | str = DEFAULT_QDRANT_PATH,
        hf_token: str | None = None,
        model_name: str = "BAAI/bge-m3",
        collection_name: str = COLLECTION_NAME,
    ):
        self.url = url or os.getenv("QDRANT_URL")
        self.api_key = api_key or os.getenv("QDRANT_API_KEY")
        self.db_path = Path(db_path)
        self.model_name = model_name
        self.hf_token = hf_token or os.getenv("HF_TOKEN")
        self.collection_name = collection_name

        # اتصال به سرور ریموت یا دیتابیس لوکال Qdrant
        if self.url:
            self.client = QdrantClient(url=self.url, api_key=self.api_key)
        else:
            self.client = QdrantClient(path=str(self.db_path))

        self._cache: dict[str, list[float]] = {}
        self._async_client = None

    def _get_async_inf_client(self):
        token = self.hf_token or os.getenv("HF_TOKEN")
        if self._async_client is None and token:
            from huggingface_hub import AsyncInferenceClient
            self._async_client = AsyncInferenceClient(token=token)
        return self._async_client


    async def get_embedding_async(self, text: str) -> list[float]:
        """
        تولید کاملاً غیرمسدودکننده (Async) بردار سوال با استفاده از AsyncInferenceClient:
        این متد هیچ‌گاه Event Loop پایتون را قفل نمی‌کند.
        """
        clean_text = text.strip()
        if clean_text in self._cache:
            return self._cache[clean_text]

        client = self._get_async_inf_client()
        if client:
            try:
                res = await client.feature_extraction(text, model=self.model_name)
                if isinstance(res, list):
                    if len(res) > 0 and isinstance(res[0], list):
                        import numpy as np
                        vec = np.mean(res, axis=0).tolist()
                    else:
                        vec = res
                else:
                    vec = list(res)
                self._cache[clean_text] = vec
                return vec
            except Exception as e:
                import logging
                logging.getLogger("qdrant_store").warning(f"Async embedding failed: {e}. Falling back to sync...")

        # در صورت نبود توکن یا خطا، در ترد مجزا سینک اجرا می‌شود تا لوپ قفل نشود
        import asyncio
        return await asyncio.to_thread(self.get_embedding, text)

    def get_embedding(self, text: str) -> list[float]:
        """
        تولید بردار برای سوال کاربر با کش محلی:
        روی سرور ۱ گیگی از API رایگان HuggingFace استفاده می‌شود تا پایتورچ لود نشود و رم اشغال نکند.
        اگر توکن تعریف نشده باشد یا به صورت محلی مدل در دسترس باشد، از sentence_transformers استفاده می‌شود.
        """
        clean_text = text.strip()
        if clean_text in self._cache:
            return self._cache[clean_text]

        # حالت اول: استفاده از API رایگان Hugging Face (مصرف رم: زیر ۲۰ مگابایت)
        token = self.hf_token or os.getenv("HF_TOKEN")
        if token:
            from huggingface_hub import InferenceClient

            inf_client = InferenceClient(token=token)
            res = inf_client.feature_extraction(text, model=self.model_name)
            if isinstance(res, list):
                if len(res) > 0 and isinstance(res[0], list):
                    import numpy as np
                    vec = np.mean(res, axis=0).tolist()
                else:
                    vec = res
            else:
                vec = list(res)
            self._cache[clean_text] = vec
            return vec

        # حالت دوم: لود محلی با sentence_transformers (صرفاً در صورت فعال‌سازی صریح با USE_LOCAL_EMBEDDING=1)
        if os.getenv("USE_LOCAL_EMBEDDING", "0") == "1":
            try:
                from sentence_transformers import SentenceTransformer
                model = SentenceTransformer(self.model_name)
                emb = model.encode(text, normalize_embeddings=True)
                return emb.tolist()
            except ImportError:
                pass

        raise RuntimeError(
            "توکن HF_TOKEN برای استخراج وکتور پیدا نشد. لطفاً مقدار HF_TOKEN را در .env قرار دهید "
            "تا از API ابری هاگینگ‌فیس با مصرف رم زیر ۶۰ مگابایت استفاده شود."
        )


    def search_by_vector(self, query_vector: list[float], limit: int = 3, min_score: float = 0.35) -> list[dict[str, Any]]:
        """جستجوی مستقیم در کیودرنت با بردار مشخص."""
        if hasattr(self.client, "query_points"):
            response = self.client.query_points(
                collection_name=self.collection_name,
                query=query_vector,
                limit=limit,
                score_threshold=min_score,
            )
            hits = response.points
        else:
            hits = self.client.search(
                collection_name=self.collection_name,
                query_vector=query_vector,
                limit=limit,
                score_threshold=min_score,
            )

        results = []
        for hit in hits:
            payload = hit.payload or {}
            results.append({
                "score": hit.score,
                "thread_id": payload.get("thread_id"),
                "text": payload.get("text"),
                "message_ids": payload.get("message_ids", []),
                "senders": payload.get("senders", []),
                "date": payload.get("date"),
                "message_links": payload.get("message_links", {}),
            })
        return results

    def search(self, query: str, limit: int = 3, min_score: float = 0.35) -> list[dict[str, Any]]:
        """جستجوی معنایی همگام تردهای مرتبط با سوال کاربر."""
        query_vector = self.get_embedding(query)
        return self.search_by_vector(query_vector, limit=limit, min_score=min_score)

    async def search_async(self, query: str, limit: int = 3, min_score: float = 0.35) -> list[dict[str, Any]]:
        """جستجوی معنایی ناهمگام (Async) تردهای مرتبط بدون بلاک کردن Event Loop."""
        import asyncio
        query_vector = await self.get_embedding_async(query)
        return await asyncio.to_thread(self.search_by_vector, query_vector, limit, min_score)

    def upsert_point(self, point_id: int | str, vector: list[float], payload: dict[str, Any]) -> None:
        """افزودن یا به‌روزرسانی آنی یک پوینت جدید در مجموعه کیودرنت."""
        from qdrant_client.models import PointStruct
        self.client.upsert(
            collection_name=self.collection_name,
            points=[PointStruct(id=point_id, vector=vector, payload=payload)],
        )


    def format_context_for_rag(self, results: list[dict[str, Any]]) -> tuple[str, dict[str, str]]:
        """
        فرمت‌بندی کانتکست جهت تزریق به مدل زبانی (LLM):
        خروجی شامل متن تردهای بازیابی شده + نقشه کلیه لینک‌های پیام‌ها برای ساخت پانویس کلیک‌خور.
        """
        if not results:
            return "هیچ تجربه یا گفتگوی مرتبطی در سوابق گروه یافت نشد.", {}

        context_parts = []
        all_links = {}

        for idx, res in enumerate(results, 1):
            context_parts.append(f"--- نتیجه {idx} (امتیاز ارتباط: {res['score']:.2f}) ---")
            context_parts.append(res["text"])
            all_links.update(res.get("message_links", {}))

        context_text = "\n\n".join(context_parts)
        return context_text, all_links
