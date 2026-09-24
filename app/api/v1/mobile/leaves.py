import os
import uuid
import shutil
from datetime import date
from fastapi import APIRouter, Depends, HTTPException, status, UploadFile, File, Form
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.database import get_session
from app.api.deps import require_student
from app.models.attendance import LeaveRequest, LeaveStatus

router = APIRouter()

# Matches the static directory we mounted in main.py
UPLOAD_DIR = "uploads"

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
        
    # 1. Secure and save the uploaded file
    file_extension = document.filename.split(".")[-1] if "." in document.filename else "jpg"
    secure_filename = f"{uuid.uuid4()}.{file_extension}"
    file_path = os.path.join(UPLOAD_DIR, secure_filename)
    
    try:
        with open(file_path, "wb") as buffer:
            shutil.copyfileobj(document.file, buffer)
    except Exception as e:
        raise HTTPException(status_code=500, detail="Failed to save document to server.")
        
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
    
    db.add(leave_request)
    await db.commit()
    await db.refresh(leave_request)
    
    return {
        "status": "success", 
        "message": "Leave request submitted successfully.",
        "leave_id": leave_request.id
    }