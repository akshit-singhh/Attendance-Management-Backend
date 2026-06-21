from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from sqlmodel.ext.asyncio.session import AsyncSession
from sqlmodel import select
from typing import List
from datetime import date
import os
import shutil
import uuid

from app.core.database import get_session
from app.api.deps import require_student
from app.models.attendance import LeaveRequest, LeaveStatus

router = APIRouter()

# Ensure the upload directory exists
UPLOAD_DIR = "uploads/medical_certificates"
os.makedirs(UPLOAD_DIR, exist_ok=True)

@router.post("/submit")
async def submit_leave_request(
    # Because it's multipart/form-data, we must use Form() and File() instead of a BaseModel
    start_date: date = Form(...),
    end_date: date = Form(...),
    reason: str = Form(...),
    file: UploadFile = File(...),
    student_id: int = Depends(require_student),
    db: AsyncSession = Depends(get_session)
):
    """Submit a medical leave with a supporting document."""
    
    # 1. Validate File Type
    allowed_types = ["image/jpeg", "image/png", "application/pdf"]
    if file.content_type not in allowed_types:
        raise HTTPException(status_code=400, detail="Only JPG, PNG, or PDF files are allowed.")
    
    # 2. Secure the filename and save the file
    # We use UUID to prevent files with the same name from overwriting each other
    file_extension = file.filename.split(".")[-1]
    secure_filename = f"{student_id}_{uuid.uuid4().hex[:8]}.{file_extension}"
    file_path = os.path.join(UPLOAD_DIR, secure_filename)
    
    with open(file_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)
        
    # In a real production app, you would upload to Supabase Storage here and get a public URL.
    # For now, we store the local relative path.
    document_url = f"/static/uploads/{secure_filename}"

    # 3. Save to Database
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
    
    return {"status": "success", "message": "Leave request submitted successfully", "leave_id": leave_request.id}

@router.get("/history")
async def get_leave_history(
    student_id: int = Depends(require_student),
    db: AsyncSession = Depends(get_session)
):
    """Fetch all past and pending leave requests for the student."""
    
    statement = (
        select(LeaveRequest)
        .where(LeaveRequest.student_id == student_id)
        .order_by(LeaveRequest.applied_on.desc())
    )
    
    results = await db.exec(statement)
    return results.all()