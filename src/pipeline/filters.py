import re

# Basic heuristic rules for a personal aggregator
SPAM_KEYWORDS = [
    "crypto", "bitcoin", "binance", "airdrop", "presale", 
    "giveaway", "free trial", "click the link", "subscribe to my channel",
    "buy now", "discount"
]

def is_spam_or_ad(text: str) -> bool:
    """
    Determines if a message is spam or an advertisement.
    Currently uses basic heuristic rules. Will be upgraded to a scikit-learn classifier later.
    """
    if not text:
        return False
        
    text_lower = text.lower()
    
    # Check for excessive links
    url_count = len(re.findall(r'http[s]?://', text_lower))
    if url_count > 2:
        return True
        
    # Check for spam keywords
    for keyword in SPAM_KEYWORDS:
        if keyword in text_lower:
            return True
            
    return False
