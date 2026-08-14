# backend/core/schema_matcher.py
# 直接复用原 schema_matcher.py（纯算法，无 Streamlit 依赖）。
# 将字段名与样本值向量化，使 Agent 能把自然语言字段描述映射到真实列名。
# 用 TF-IDF（字符级 n-gram）而非 embedding：零 API 成本，短 token 效果好。
import numpy as np
import pandas as pd
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity


class SchemaIndex:
    """每个加载的 DataFrame 构建一次，构造后不可变。"""

    def __init__(self, df: pd.DataFrame):
        self.columns: list[str] = list(df.columns)
        self._docs: list[str]   = self._build_docs(df)
        self._vectorizer        = TfidfVectorizer(
            analyzer="char_wb", ngram_range=(2, 4), min_df=1
        )
        self._matrix = self._vectorizer.fit_transform(self._docs)

    def match(self, query: str, top_k: int = 3) -> list[tuple[str, float]]:
        """返回 [(列名, 相关度), ...]，按相关度降序。"""
        if not query.strip():
            return []
        q_vec  = self._vectorizer.transform([query])
        scores = cosine_similarity(q_vec, self._matrix).flatten()
        ranked = sorted(
            zip(self.columns, scores.tolist()),
            key=lambda x: x[1],
            reverse=True,
        )
        return [(col, round(score, 4)) for col, score in ranked[:top_k] if score > 0]

    def resolve(self, query: str, threshold: float = 0.15) -> str | None:
        hits = self.match(query, top_k=1)
        if hits and hits[0][1] >= threshold:
            return hits[0][0]
        return None

    def _build_docs(self, df: pd.DataFrame) -> list[str]:
        docs = []
        for col in df.columns:
            dtype_tag = self._dtype_tag(df[col])
            samples   = df[col].dropna().astype(str).head(5).tolist()
            doc = f"{col} {dtype_tag} {' '.join(samples)}"
            docs.append(doc)
        return docs

    @staticmethod
    def _dtype_tag(series: pd.Series) -> str:
        if pd.api.types.is_datetime64_any_dtype(series):
            return "日期 date datetime 时间"
        if pd.api.types.is_numeric_dtype(series):
            return "数值 number 金额 数量"
        return "文本 类别 category text"
