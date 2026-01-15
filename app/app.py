from fastapi import FastAPI, HTTPException, File, UploadFile, Form, Depends
from app.schemas import PostCreate, UserRead, UserCreate, UserUpdate
from app.db import Post, create_db_and_tables, get_async_session, User
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from contextlib import asynccontextmanager
from app.images import imagekit
from imagekitio.models.UploadFileRequestOptions import UploadFileRequestOptions
import shutil
import uuid
import os
import tempfile
from app.users import auth_backend, current_active_user, fastapi_users



@asynccontextmanager
async def lifespan(api: FastAPI):
    await create_db_and_tables()
    yield

api = FastAPI(lifespan=lifespan)


api.include_router(fastapi_users.get_auth_router(auth_backend), prefix='/auth/jwt', tags=["auth"])
api.include_router(fastapi_users.get_register_router(UserRead, UserCreate), prefix='/auth', tags=["auth"])
api.include_router(fastapi_users.get_reset_password_router(), prefix='/auth', tags=["auth"])
api.include_router(fastapi_users.get_verify_router(UserRead), prefix="/auth", tags=["auth"])
api.include_router(fastapi_users.get_users_router(UserRead, UserUpdate), prefix="/users", tags=['users'])



@api.post('/upload')
async def upload_file( file: UploadFile = File(...),
                       caption: str = Form(''),
                       user: User = Depends(current_active_user),
                       session: AsyncSession = Depends(get_async_session)       #this is a Dependency Injection, basically were getting the async session from db in the session variable
                     ):
    
    temp_file_path = None
    
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=os.path.splitext(file.filename)[1]) as temp_file:
            temp_file_path = temp_file.name
            shutil.copyfileobj(file.file , temp_file)

        upload_result = imagekit.upload_file(
            file = open(temp_file_path, "rb"),
            file_name = file.filename,
            options= UploadFileRequestOptions(
                use_unique_file_name = True,
                tags = ["backend-upload"]
                )
        )

        if upload_result.response_metadata.http_status_code == 200:

            post = Post(
                caption = caption,
                user_id = user.id,
                url = upload_result.url,
                file_id = str(upload_result.file_id),
                file_type = "video" if file.content_type.startswith("video/") else "image", 
                file_name = upload_result.name
            )
            session.add(post)
            await session.commit()
            await session.refresh(post)
            return post
    
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    
    finally:
        if temp_file_path and os.path.exists(temp_file_path):
            os.unlink(temp_file_path)
        file.file.close()



@api.get('/feed')
async def get_feed( session: AsyncSession = Depends(get_async_session), user: User = Depends(current_active_user) ):

    # result = await session.query(Post).order_by(Post.created_at.desc()).all()
    # posts = [row[0] for row in result]

    result = await session.execute(select(Post).order_by(Post.created_at.desc()))
    posts = [row[0] for row in result.all()]


    result = await session.execute(select(User))
    users = [row[0] for row in result.all()]
    user_dict = {u.id : u.email for u in users}


    posts_data = []

    for post in posts:
        posts_data.append(
            {
              'id' : str(post.id),
              'user_id' : str(post.user_id),
              'file_id' : post.file_id,
              'caption' : post.caption,
              'url' : post.url,
              'file_type' : post.file_type,
              'file_name' : post.file_name,
              'created_at' : post.created_at.isoformat(),
              'is_owner' : post.user_id == user.id,
              'email' : user_dict.get(post.user_id, 'unknown')
            }
        )
    return {"posts" : posts_data}


@api.delete('/posts/{post_id}')
async def delete_post( post_id: str, session: AsyncSession = Depends(get_async_session), user: User = Depends(current_active_user)):
    try: 
        post_uuid = uuid.UUID(post_id)

        result = await session.execute(select(Post).where(Post.id == post_uuid))
        post = result.scalars().first()

        if not post:
            raise HTTPException(status_code=404, detail="Post not found")
        
        if post.user_id != user.id:
            raise HTTPException(status_code=403, detail="now allowed to delete")
        

        file_id = post.file_id

        delete_result = imagekit.delete_file(file_id)
        

        await session.delete(post)
        await session.commit()

        return {"success": True, "messgae" : "Post deleted successfully", 'imagekit_delete_obj' : delete_result}
    
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    



