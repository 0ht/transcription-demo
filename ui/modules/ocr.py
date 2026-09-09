# modules/ocr.py  
import os  
import datetime  
import logging
import tempfile  
from pathlib import Path  
  
from azure.storage.blob import BlobServiceClient  
from azure.core.credentials import AzureKeyCredential  
from azure.identity import DefaultAzureCredential  
from azure.ai.documentintelligence import DocumentIntelligenceClient  
from azure.ai.documentintelligence.models import DocumentAnalysisFeature  
  
from modules.config import (  
    BLOB_CONNECTION_STRING,  
    STORAGE_ACCOUNT_NAME,  
    UPLOAD_CONTAINER_NAME,  
    CHUNK_CONTAINER_NAME,  
    ADI_ENDPOINT,  
    ADI_KEY,  
    ADI_MODEL,  
)  
  
OCR_SUPPORTED_EXTS = {  
    ".pdf", ".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp"  
}  

logger = logging.getLogger(__name__)
  
_blob_service_client = None  
_adi_client = None  
  
  
def get_blob_service_client() -> BlobServiceClient:  
    global _blob_service_client  
    if _blob_service_client is None:  
        if BLOB_CONNECTION_STRING:  
            _blob_service_client = BlobServiceClient.from_connection_string(  
                BLOB_CONNECTION_STRING  
            )  
        else:  
            default_credential = DefaultAzureCredential()  
            _blob_service_client = BlobServiceClient(  
                account_url=f"https://{STORAGE_ACCOUNT_NAME}.blob.core.windows.net",  
                credential=default_credential,  
            )  
    return _blob_service_client  
  
  
def get_adi_client() -> DocumentIntelligenceClient:  
    global _adi_client  
    if _adi_client is None:  
        if ADI_KEY:  
            adi_credential = AzureKeyCredential(ADI_KEY)  
        else:  
            adi_credential = DefaultAzureCredential()  
  
        _adi_client = DocumentIntelligenceClient(  
            endpoint=ADI_ENDPOINT,  
            credential=adi_credential,  
        )  
    return _adi_client  
  
  
def get_upload_container_client():  
    return get_blob_service_client().get_container_client(UPLOAD_CONTAINER_NAME)  
  
  
def get_chunk_container_client():  
    return get_blob_service_client().get_container_client(CHUNK_CONTAINER_NAME)  
  
  
def _sort_blob_items(items: list[dict]) -> list[dict]:  
    return sorted(  
        items,  
        key=lambda x: x.get("last_modified").timestamp() if x.get("last_modified") else 0,  
        reverse=True,  
    )  
  
  
def upload_file_to_ocr_input(file_name: str, data: bytes) -> str:  
    ext = Path(file_name).suffix.lower()  
    if ext not in OCR_SUPPORTED_EXTS:  
        raise ValueError(f"非対応形式です: {ext}")  
  
    container = get_upload_container_client()  
    container.upload_blob(file_name, data, overwrite=True)  
    return file_name  
  
  
def list_uploaded_files() -> list[dict]:  
    container = get_upload_container_client()  
    results = []  
  
    for blob in container.list_blobs():  
        results.append({  
            "name": blob.name,  
            "size": blob.size,  
            "last_modified": blob.last_modified,  
        })  
  
    return _sort_blob_items(results)  
  
  
def process_ocr_from_blob(blob_name: str) -> str:  
    """  
    upload コンテナ上の blob をダウンロードして OCR 実行し、  
    markdown を chunk コンテナに保存する。  
  
    Returns:  
        保存した OCR 結果 blob 名  
    """  
    upload_container = get_upload_container_client()  
    chunk_container = get_chunk_container_client()  
    adi_client = get_adi_client()  
  
    temp_file_path = None  
    try:  
        logger.info("OCR started: blob=%s model=%s", blob_name, ADI_MODEL)
        suffix = os.path.splitext(blob_name)[1]  
  
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as temp_file:  
            blob_data = upload_container.download_blob(blob_name).readall()  
            temp_file.write(blob_data)  
            temp_file_path = temp_file.name  
  
        with open(temp_file_path, "rb") as f:  
            poller = adi_client.begin_analyze_document(  
                model_id=ADI_MODEL,  
                body=f,  
                locale="ja-JP",  
                features=[DocumentAnalysisFeature.OCR_HIGH_RESOLUTION],  
                content_type="application/octet-stream",  
                output_content_format="markdown",  
            )  
            result = poller.result()  
  
        markdown_content = result.content or ""  
  
        output_blob_name = (  
            f"{os.path.splitext(blob_name)[0]}_ocr_"  
            f"{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.md"  
        )  
  
        chunk_container.upload_blob(  
            output_blob_name,  
            markdown_content.encode("utf-8"),  
            overwrite=True,  
        )  

        logger.info(
            "OCR completed: input_blob=%s result_blob=%s characters=%d",
            blob_name,
            output_blob_name,
            len(markdown_content),
        )
  
        return output_blob_name  

    except Exception:
        logger.exception("OCR failed: blob=%s", blob_name)
        raise
  
    finally:  
        if temp_file_path and os.path.exists(temp_file_path):  
            os.remove(temp_file_path)  
  
  
def list_ocr_results() -> list[dict]:  
    container = get_chunk_container_client()  
    results = []  
  
    for blob in container.list_blobs():  
        results.append({  
            "name": blob.name,  
            "size": blob.size,  
            "last_modified": blob.last_modified,  
        })  
  
    return _sort_blob_items(results)  
  
  
def load_ocr_result_text(blob_name: str) -> str:  
    container = get_chunk_container_client()  
    data = container.download_blob(blob_name).readall()  
    return data.decode("utf-8")  
  
  
def delete_uploaded_file(blob_name: str) -> None:  
    container = get_upload_container_client()  
    container.delete_blob(blob_name, delete_snapshots="include")  
  
  
def delete_ocr_result(blob_name: str) -> None:  
    container = get_chunk_container_client()  
    container.delete_blob(blob_name, delete_snapshots="include")  