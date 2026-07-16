import logging
from sentence_transformers import SentenceTransformer
from gliner import GLiNER
from typing import List, Dict, Any

logger = logging.getLogger(__name__)

# Initialize models lazily to avoid heavy loading if not needed immediately
_embedding_model = None
_gliner_model = None

def get_embedding_model():
    global _embedding_model
    if _embedding_model is None:
        logger.info("Loading SentenceTransformer (multilingual-e5-small)...")
        # Ensure we add "query: " or "passage: " if using e5 models, 
        # but for simple semantic similarity between short texts, the base works fine.
        _embedding_model = SentenceTransformer("intfloat/multilingual-e5-small", device='cpu')
    return _embedding_model

def get_gliner_model():
    global _gliner_model
    if _gliner_model is None:
        logger.info("Loading GLiNER (multilingual)...")
        # using a lightweight version if available, otherwise base multilingual
        _gliner_model = GLiNER.from_pretrained("urchade/gliner_multi-v2.1")
    return _gliner_model

def generate_embedding(text: str) -> List[float]:
    """
    Generates a 384-dimensional embedding vector for the given text.
    """
    # e5 models usually expect "passage: " prefix for indexing and "query: " for search
    prefix = "passage: "
    model = get_embedding_model()
    
    # We use numpy to list conversion
    embedding = model.encode(f"{prefix}{text}", normalize_embeddings=True)
    return embedding.tolist()

def extract_entities(text: str) -> List[Dict[str, Any]]:
    """
    Extracts Location, Person, Equipment, and Organization from text.
    """
    model = get_gliner_model()
    labels = ["Location", "Person", "Equipment", "Organization"]
    
    # Predict entities
    entities = model.predict_entities(text, labels)
    return entities
