#!/usr/bin/env python
"""Diagnostic script to verify embedding service works correctly."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

print("=" * 60)
print("EMBEDDING SERVICE DIAGNOSTIC")
print("=" * 60)
print()

try:
    from app.services.embedding_service import embedding_service
    
    print("✓ Import successful")
    print()
    
    # Check methods
    print("Available methods:")
    print(f"  embed_batch: {hasattr(embedding_service, 'embed_batch')}")
    print(f"  embed_text: {hasattr(embedding_service, 'embed_text')}")
    print(f"  embed (old method, should be False): {hasattr(embedding_service, 'embed')}")
    print()
    
    # Check mode
    print(f"Mock mode: {embedding_service.mock}")
    print(f"Model: {embedding_service.model if not embedding_service.mock else 'N/A (mock)'}")
    print()
    
    # Test embedding
    print("Testing embed_batch with sample text...")
    result = embedding_service.embed_batch(["test text 1", "test text 2"])
    print(f"✓ Success: {len(result)} embeddings returned")
    print(f"  Embedding dimension: {len(result[0])}")
    print()
    
    if embedding_service.mock:
        print("⚠ WARNING: Embeddings are in MOCK mode")
        print("  This means semantic matching will not work properly.")
        print("  Set OPENAI_API_KEY in .env to enable real embeddings.")
        print()
    
    print("=" * 60)
    print("DIAGNOSIS: Embedding service is working correctly")
    print("=" * 60)
    
except AttributeError as e:
    print(f"✗ ERROR: {e}")
    print()
    print("This error suggests you have a cached .pyc file with old code.")
    print()
    print("Fix:")
    print("  1. Clear Python cache:")
    print("     find . -type d -name '__pycache__' -exec rm -rf {} +")
    print("     find . -type f -name '*.pyc' -delete")
    print("  2. Re-run this diagnostic")
    print()
    sys.exit(1)
    
except Exception as e:
    print(f"✗ UNEXPECTED ERROR: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)
