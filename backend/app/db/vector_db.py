import chromadb
from chromadb.config import Settings
import os

CHROMA_DB_DIR = os.getenv("CHROMA_DB_DIR", "./chroma_db")

def get_vector_client():
    client = chromadb.PersistentClient(path=CHROMA_DB_DIR)
    return client

def get_or_create_collection(name: str):
    client = get_vector_client()
    return client.get_or_create_collection(name=name)
