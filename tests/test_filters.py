import pytest
from src.pipeline.filters import is_spam_or_ad

def test_is_spam_or_ad_empty():
    assert not is_spam_or_ad("")
    assert not is_spam_or_ad(None)

def test_is_spam_or_ad_normal_text():
    assert not is_spam_or_ad("Russian missiles struck Kharkiv tonight. Heavy damages reported.")

def test_is_spam_or_ad_keywords():
    assert is_spam_or_ad("Buy bitcoin now for 100x gains!")
    assert is_spam_or_ad("Join my crypto giveaway.")
    assert is_spam_or_ad("Subscribe to my channel for more news.")

def test_is_spam_or_ad_excessive_links():
    assert is_spam_or_ad("Read more here: https://link1.com, and here https://link2.com, and also https://link3.com")
