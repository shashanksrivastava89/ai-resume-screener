"""Embedding tests (P7): batching, caching, cosine math. Offline (fake client)."""

from types import SimpleNamespace

from resume_screener.cache import JsonCache
from resume_screener.embed import clamped_cosine01, cosine, embed_texts


class FakeEmbeddings:
    """Counts API calls; returns a deterministic vector per input (its length)."""

    def __init__(self):
        self.calls = 0

        def create(model, input):
            self.calls += 1
            data = [
                SimpleNamespace(index=i, embedding=[float(len(text)), 1.0])
                for i, text in enumerate(input)
            ]
            return SimpleNamespace(data=data)

        self.embeddings = SimpleNamespace(create=create)


def test_batching_and_caching(tmp_path):
    cache = JsonCache(tmp_path)
    client = FakeEmbeddings()

    texts = ["aa", "bbb", "cccc", "ddddd", "e"]
    vectors = embed_texts(texts, client, "emb-test", cache, batch_size=2)
    assert vectors == [[2.0, 1.0], [3.0, 1.0], [4.0, 1.0], [5.0, 1.0], [1.0, 1.0]]
    assert client.calls == 3  # 5 misses in chunks of 2 -> 3 batched calls, never 5

    # Second run: everything cached, zero API calls.
    again = embed_texts(texts, client, "emb-test", cache, batch_size=2)
    assert again == vectors and client.calls == 3

    # A new text triggers exactly one more call; cached ones stay cached.
    mixed = embed_texts(["aa", "zz-new"], client, "emb-test", cache, batch_size=2)
    assert client.calls == 4 and mixed[0] == [2.0, 1.0]

    # Different model/deployment = different cache key = fresh call.
    embed_texts(["aa"], client, "other-model", cache, batch_size=2)
    assert client.calls == 5


def test_cosine_math():
    assert cosine([1.0, 0.0], [1.0, 0.0]) == 1.0
    assert cosine([1.0, 0.0], [0.0, 1.0]) == 0.0
    assert cosine([0.0, 0.0], [1.0, 0.0]) == 0.0  # zero vector -> 0, never NaN
    assert clamped_cosine01([1.0, 0.0], [-1.0, 0.0]) == 0.0  # clamped into 0..1
    assert clamped_cosine01([2.0, 0.0], [4.0, 0.0]) == 1.0
