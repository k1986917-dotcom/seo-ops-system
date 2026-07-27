from seo_ops.db import connection
from seo_ops.ingest import import_cms_bytes
from seo_ops.services.topic_graph import sync_topic_graph, topic_tree
from tests.helpers import blog_export_bytes, product_export_bytes


def test_topic_graph_maps_each_content_once_and_is_idempotent(settings):
    import_cms_bytes(1, "blogs.json", blog_export_bytes(), settings)
    import_cms_bytes(1, "products.json", product_export_bytes(), settings)

    first = sync_topic_graph(1, settings)
    second = sync_topic_graph(1, settings)
    tree = topic_tree(1, settings)

    assert first.mapped_content_count == 3
    assert first.primary_count == 3
    assert first.unmapped_content_count == 0
    assert second.primary_count == 3
    assert tree[0]["topic_key"] == "laser-pointers"
    assert tree[0]["article_count"] == 3

    with connection(settings) as conn:
        duplicate_primary = conn.execute(
            """
            SELECT content_item_id, COUNT(*) AS count
            FROM topic_content_links
            WHERE coverage_role = 'primary'
            GROUP BY content_item_id
            HAVING COUNT(*) <> 1
            """
        ).fetchall()
        product_parent = conn.execute(
            """
            SELECT parent.topic_key
            FROM topic_content_links link
            JOIN content_items content ON content.id = link.content_item_id
            JOIN topic_nodes article ON article.id = link.topic_id
            JOIN topic_nodes parent ON parent.id = article.parent_id
            WHERE link.coverage_role = 'primary' AND content.content_type = 'product'
            """
        ).fetchone()
        product_url = conn.execute(
            "SELECT canonical_url FROM content_items WHERE content_type = 'product'"
        ).fetchone()["canonical_url"]

    assert duplicate_primary == []
    assert product_parent["topic_key"] == "product-models"
    assert product_url == "https://laserpointerhub.com/p-DEMO-1.html"


def test_support_knowledge_does_not_replace_primary_article_topic(settings):
    import_cms_bytes(1, "blogs.json", blog_export_bytes(), settings)
    sync_topic_graph(1, settings)

    with connection(settings) as conn:
        roles = conn.execute(
            "SELECT coverage_role, COUNT(*) AS count FROM topic_content_links GROUP BY coverage_role"
        ).fetchall()

    assert {row["coverage_role"]: row["count"] for row in roles}["primary"] == 2
