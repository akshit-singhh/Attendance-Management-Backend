
import uuid
import logging
from datetime import date
from pathlib import Path
from fastapi import APIRouter, Depends, HTTPException, status, UploadFile, File, Form
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.database import get_session
from app.api.deps import require_student
from app.models.attendance import LeaveRequest, LeaveStatus

router = APIRouter()

# Matches the static directory we mounted in main.py
UPLOAD_DIR = Path("uploads")
MAX_DOCUMENT_BYTES = 10 * 1024 * 1024
ALLOWED_DOCUMENTS = {
    ".pdf": {"application/pdf"},
    ".jpg": {"image/jpeg"},
    ".jpeg": {"image/jpeg"},
    ".png": {"image/png"},
}
logger = logging.getLogger(__name__)

@router.post("/apply", status_code=status.HTTP_201_CREATED)
async def apply_for_leave(
    # Using Form() and File() forces FastAPI to parse this as multipart/form-data
    start_date: date = Form(...),
    end_date: date = Form(...),
    reason: str = Form(...),
    document: UploadFile = File(...),
    student_id: int = Depends(require_student),
    db: AsyncSession = Depends(get_session)
):
    """Submit a medical leave request with a supporting document."""
    
    if start_date > end_date:
        raise HTTPException(status_code=400, detail="Start date cannot be after end date.")
        
    # Use an allowlisted extension and MIME type; never trust the client filename.
    file_extension = Path(document.filename or "").suffix.lower()
    allowed_mime_types = ALLOWED_DOCUMENTS.get(file_extension)
    if not allowed_mime_types or document.content_type not in allowed_mime_types:
        raise HTTPException(status_code=400, detail="Upload a PDF, JPEG, or PNG document.")

    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    secure_filename = f"{uuid.uuid4().hex}{file_extension}"
    file_path = UPLOAD_DIR / secure_filename
    total_bytes = 0
    header = bytearray()
    
    try:
        with file_path.open("wb") as buffer:
            while chunk := await document.read(64 * 1024):
                total_bytes += len(chunk)
                if total_bytes > MAX_DOCUMENT_BYTES:
                    raise HTTPException(status_code=413, detail="Document must be 10 MB or smaller.")
                if len(header) < 8:
                    header.extend(chunk[: 8 - len(header)])
                buffer.write(chunk)
        signatures = {
            ".pdf": bytes(header).startswith(b"%PDF-"),
            ".jpg": bytes(header).startswith(b"\xff\xd8\xff"),
            ".jpeg": bytes(header).startswith(b"\xff\xd8\xff"),
            ".png": bytes(header).startswith(b"\x89PNG\r\n\x1a\n"),
        }
        if total_bytes == 0 or not signatures[file_extension]:
            raise HTTPException(status_code=400, detail="The uploaded document is empty or its contents do not match its file type.")
    except HTTPException:
        file_path.unlink(missing_ok=True)
        raise
    except OSError:
        logger.exception("Failed to save leave document")
        file_path.unlink(missing_ok=True)
        raise HTTPException(status_code=500, detail="Failed to save document to server.")
    finally:
        await document.close()
        
    # 2. Construct the public URL for the React dashboard
    document_url = f"/static/uploads/{secure_filename}"
    
    # 3. Create the Database Record
    leave_request = LeaveRequest(
        student_id=student_id,
        start_date=start_date,
        end_date=end_date,
        reason=reason,
        document_url=document_url,
        status=LeaveStatus.PENDING
    )
    
    try:
        db.add(leave_request)
        await db.commit()
        await db.refresh(leave_request)
    except Exception:
        await db.rollback()
        file_path.unlink(missing_ok=True)
        logger.exception("Failed to persist leave request; removed uploaded document")
        raise HTTPException(status_code=500, detail="Failed to submit leave request.")
    
    return {
        "status": "success", 
        "message": "Leave request submitted successfully.",
        "leave_id": leave_request.id
    }
