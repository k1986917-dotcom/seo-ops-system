"""
Cannibalization Checker — 基于 TF-IDF 的离线语义防蚕食检测器

用法:
  python3 data_sources/modules/cannibalization_checker.py <website> [--threshold 0.45]

示例:
  python3 data_sources/modules/cannibalization_checker.py laserpointerhub
  python3 data_sources/modules/cannibalization_checker.py laserpointerhub --threshold 0.50

原理:
  - 读取 {website}/published/ 下所有已发布文章正文
  - 计算 TF-IDF 向量
  - 对所有文章对计算余弦相似度
  - 相似度 >= threshold 的对标记为蚕食风险
  - 输出报告供 /research 和 /write 流程使用
"""

import argparse
import math
import os
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Dict, List, Optional, Tuple

SITES_DIR = Path(os.environ.get('SEO_SITES_DIR') or Path(__file__).resolve().parents[2])

try:
    import seo_config
except ImportError:
    import importlib.util as _ilu
    _spec = _ilu.spec_from_file_location('seo_config', Path(__file__).resolve().parent / 'seo_config.py')
    seo_config = _ilu.module_from_spec(_spec)
    _spec.loader.exec_module(seo_config)


def _tokenize(text: str) -> List[str]:
    """简易英文分词 + 去停用词"""
    stop_words = {
        'the', 'a', 'an', 'is', 'are', 'was', 'were', 'be', 'been', 'being',
        'have', 'has', 'had', 'do', 'does', 'did', 'will', 'would', 'could',
        'should', 'may', 'might', 'can', 'shall', 'to', 'of', 'in', 'for',
        'on', 'with', 'at', 'by', 'from', 'as', 'into', 'through', 'during',
        'before', 'after', 'above', 'below', 'between', 'under', 'again',
        'further', 'then', 'once', 'here', 'there', 'when', 'where', 'why',
        'how', 'all', 'both', 'each', 'few', 'more', 'most', 'other', 'some',
        'such', 'no', 'nor', 'not', 'only', 'own', 'same', 'so', 'than',
        'too', 'very', 'just', 'because', 'but', 'and', 'or', 'if', 'while',
        'it', 'its', 'this', 'that', 'these', 'those', 'i', 'me', 'my',
        'we', 'our', 'you', 'your', 'he', 'she', 'they', 'them', 'their',
        'what', 'which', 'who', 'whom', 'about', 'up', 'out', 'also',
        'one', 'two', 'get', 'use', 'make', 'like', 'even', 'much', 'many'
    }
    words = re.findall(r'[a-z]{2,}', text.lower())
    return [w for w in words if w not in stop_words]


class CannibalizationChecker:
    """TF-IDF 语义防蚕食检测器"""

    def __init__(self, threshold: float = None):
        if threshold is None:
            threshold = seo_config.CANNIBAL_THRESHOLD_LOW
        self.threshold = threshold
        self.documents: Dict[str, str] = {}       # slug -> raw body
        self.tokens: Dict[str, List[str]] = {}    # slug -> token list
        self.tfidf: Dict[str, Dict[str, float]] = {}  # slug -> {term: weight}

    def load_published(self, website: str) -> int:
        """加载已发布文章正文（跳过 frontmatter）"""
        published_dir = SITES_DIR / website / 'published'
        if not published_dir.exists():
            print(f"❌ 目录不存在: {published_dir}")
            return 0

        for filepath in sorted(published_dir.glob("*.md")):
            slug = filepath.stem
            text = filepath.read_text(encoding='utf-8')

            # 跳过 YAML frontmatter
            if text.startswith('---'):
                parts = text.split('---', 2)
                if len(parts) >= 3:
                    body = parts[2]
                else:
                    body = text
            else:
                body = text

            # 清理 markdown
            body = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', body)  # 链接留文字
            body = re.sub(r'[#*>`|]', '', body)                     # markdown 符号
            body = re.sub(r'\s+', ' ', body).strip()

            if len(body.split()) < 100:
                continue  # 太短跳过

            self.documents[slug] = body
            self.tokens[slug] = _tokenize(body)

        print(f"📚 已加载 {len(self.documents)} 篇文章正文")
        return len(self.documents)

    def _compute_tf(self, tokens: List[str]) -> Dict[str, float]:
        """计算词频"""
        counter = Counter(tokens)
        total = len(tokens)
        return {term: count / total for term, count in counter.items()}

    def _compute_idf(self) -> Dict[str, float]:
        """计算逆文档频率"""
        n_docs = len(self.tokens)
        df: Dict[str, int] = {}
        for tokens in self.tokens.values():
            for term in set(tokens):
                df[term] = df.get(term, 0) + 1
        return {term: math.log((n_docs + 1) / (count + 1)) + 1 for term, count in df.items()}

    def build_index(self):
        """构建 TF-IDF 索引"""
        idf = self._compute_idf()
        for slug, tokens in self.tokens.items():
            tf = self._compute_tf(tokens)
            self.tfidf[slug] = {term: tf_val * idf.get(term, 0) for term, tf_val in tf.items()}

    def _cosine_similarity(self, vec1: Dict[str, float], vec2: Dict[str, float]) -> float:
        """余弦相似度"""
        dot = sum(vec1.get(k, 0) * vec2.get(k, 0) for k in set(vec1) | set(vec2))
        norm1 = math.sqrt(sum(v ** 2 for v in vec1.values()))
        norm2 = math.sqrt(sum(v ** 2 for v in vec2.values()))
        if norm1 == 0 or norm2 == 0:
            return 0.0
        return dot / (norm1 * norm2)

    def check_all(self) -> List[Dict]:
        """对所有文章对计算相似度，返回超过阈值的对"""
        slugs = list(self.documents.keys())
        results = []

        for i in range(len(slugs)):
            for j in range(i + 1, len(slugs)):
                sim = self._cosine_similarity(
                    self.tfidf[slugs[i]], self.tfidf[slugs[j]]
                )
                if sim >= self.threshold:
                    results.append({
                        'slug_a': slugs[i],
                        'slug_b': slugs[j],
                        'similarity': round(sim, 3),
                        'risk': ('🔴 HIGH' if sim >= seo_config.CANNIBAL_THRESHOLD_HIGH
                                 else ('🟡 MEDIUM' if sim >= seo_config.CANNIBAL_THRESHOLD_LOW
                                       else '🟢 LOW'))
                    })

        results.sort(key=lambda x: -x['similarity'])
        return results

    def check_against_all(self, new_slug: str, new_body: str) -> List[Dict]:
        """将新文章与所有已有文章比对"""
        new_tokens = _tokenize(new_body)
        if len(new_tokens) < 20:
            return []

        new_tf = self._compute_tf(new_tokens)
        idf = self._compute_idf()
        new_vec = {term: tf_val * idf.get(term, 0) for term, tf_val in new_tf.items()}

        results = []
        for slug, vec in self.tfidf.items():
            sim = self._cosine_similarity(new_vec, vec)
            results.append({
                'existing_slug': slug,
                'similarity': round(sim, 3),
                'risk': ('🔴 HIGH' if sim >= seo_config.CANNIBAL_THRESHOLD_HIGH
                         else ('🟡 MEDIUM' if sim >= seo_config.CANNIBAL_THRESHOLD_LOW
                               else '🟢 SAFE'))
            })

        results.sort(key=lambda x: -x['similarity'])
        return results

    def format_report(self, results: List[Dict]) -> str:
        """格式化输出报告"""
        lines = [
            "=" * 60,
            "CANNIBALIZATION CHECK — TF-IDF Semantic Analysis",
            "=" * 60,
            "",
            f"Threshold: {self.threshold}  |  Pairs flagged: {len(results)}",
            ""
        ]

        if not results:
            lines.append("✅ No cannibalization risks detected.")
            return "\n".join(lines)

        for r in results:
            if 'slug_a' in r:
                lines.append(f"{r['risk']}  similarity={r['similarity']}")
                lines.append(f"    A: {r['slug_a']}")
                lines.append(f"    B: {r['slug_b']}")
            else:
                lines.append(f"{r['risk']}  similarity={r['similarity']}")
                lines.append(f"    vs: {r['existing_slug']}")
            lines.append("")

        return "\n".join(lines)


def main():
    """CLI entry point.

    Replaced the hand-rolled sys.argv scanning with argparse so the interface is
    explicit and `--help` works. Behaviour is preserved: website is positional
    (required), threshold defaults to CANNIBAL_THRESHOLD_LOW, optional
    --new-article and --tags flags apply the same filters as before.
    """
    ap = argparse.ArgumentParser(description='TF-IDF cannibalization checker')
    ap.add_argument('website', help='website subdirectory (e.g. laserpointerhub)')
    ap.add_argument('--threshold', type=float, default=None,
                    help='cosine similarity threshold (default: %(default)s)')
    ap.add_argument('--new-article', dest='new_article', help='draft path to compare vs published')
    ap.add_argument('--tags', help='comma-separated keywords to filter slugs')
    args = ap.parse_args()

    threshold = args.threshold if args.threshold is not None else seo_config.CANNIBAL_THRESHOLD_LOW

    checker = CannibalizationChecker(threshold=threshold)
    count = checker.load_published(args.website)

    if count < 2:
        print("⚠️  文章少于 2 篇，跳过检测")
        sys.exit(0)

    checker.build_index()

    # 新文章 vs 已有文章 比对
    if args.new_article:
        path = Path(args.new_article)
        if not path.exists():
            print(f"❌ 文件不存在: {args.new_article}")
            sys.exit(1)
        new_body = path.read_text(encoding='utf-8')
        new_slug = path.stem
        results = checker.check_against_all(new_slug, new_body)

        # 标签过滤
        if args.tags:
            tags = set(t.strip().lower() for t in args.tags.split(','))
            results = [r for r in results
                       if any(t in r['existing_slug'].lower() for t in tags)]

        print(checker.format_report(results))
    else:
        # 全量交叉比对
        results = checker.check_all()

        # 标签过滤（按 slug 关键词过滤，用于只看特定话题簇）
        if args.tags:
            tags = set(t.strip().lower() for t in args.tags.split(','))
            results = [r for r in results
                       if any(t in r['slug_a'].lower() or t in r['slug_b'].lower()
                              for t in tags)]

        print(checker.format_report(results))


if __name__ == '__main__':
    main()
