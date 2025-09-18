from fastapi import FastAPI, Depends, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from sqlalchemy import create_engine, Column, Integer, String, Float, DateTime, func, ForeignKey
from sqlalchemy.orm import sessionmaker, declarative_base, relationship, Session
from passlib.context import CryptContext
from jose import jwt
from datetime import datetime, timedelta
from typing import Optional
import uvicorn
# ---------- DB setup ----------
SQLALCHEMY_DATABASE_URL = "sqlite:///./exp.db"
engine = create_engine(SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()
class User(Base):
 __tablename__ = "users"
 id = Column(Integer, primary_key=True, index=True)
 email = Column(String, unique=True, index=True, nullable=False)
 hashed_password = Column(String, nullable=False)
 expenses = relationship("Expense", back_populates="owner", cascade="all,delete")
class Expense(Base):
 __tablename__ = "expenses"
 id = Column(Integer, primary_key=True, index=True)
 category = Column(String, index=True, nullable=False)
 amount = Column(Float, nullable=False)
 comments = Column(String, nullable=True)
 created_at = Column(DateTime, server_default=func.now(), nullable=False)
 updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now(), nullable=False)
 owner_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
 owner = relationship("User", back_populates="expenses")
Base.metadata.create_all(bind=engine)
def get_db():
 db = SessionLocal()
 try:
     yield db
 finally:
     db.close()
# ---------- Auth setup ----------
SECRET_KEY = "change-this-in-production"
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login")
def hash_password(p: str) -> str:
 return pwd_context.hash(p)
def verify_password(plain: str, hashed: str) -> bool:
 return pwd_context.verify(plain, hashed)
def create_access_token(data: dict, minutes: int = ACCESS_TOKEN_EXPIRE_MINUTES) -> str:
 to_encode = data.copy()
 to_encode.update({"exp": datetime.utcnow() + timedelta(minutes=minutes)})
 return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
def current_user(token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)) -> User:
   try:
       payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
       uid = int(payload.get("sub"))
   except Exception:
       raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")
   user = db.query(User).filter(User.id == uid).first()
   if not user:
       raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")
   return user
# ---------- FastAPI app ----------
app = FastAPI(title="Expense Tracker (Single File)")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])
# ---------- Auth routes ----------
@app.post("/auth/signup")
def signup(email: str, password: str, db: Session = Depends(get_db)):
    if db.query(User).filter(User.email == email).first():
        raise HTTPException(status_code=400, detail="Email already registered")
    user = User(email=email, hashed_password=hash_password(password))
    db.add(user)
    db.commit()
    db.refresh(user)
    return {"id": user.id, "email": user.email}
@app.post("/auth/login")
def login(form: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)):
 user = db.query(User).filter(User.email == form.username).first()
 if not user or not verify_password(form.password, user.hashed_password):
     raise HTTPException(status_code=400, detail="Incorrect email or password")
 token = create_access_token({"sub": str(user.id)})
 return {"access_token": token, "token_type": "bearer"}
# ---------- Expense routes ----------
@app.post("/expenses/")
def add_expense(category: str, amount: float, comments: Optional[str] = None, db: Session = Depends(get_db), user: User = Depends(current_user)):
    exp = Expense(category=category, amount=amount, comments=comments, owner_id=user.id)
    db.add(exp)
    db.commit()
    db.refresh(exp)
    return exp
@app.get("/expenses/")
def list_expenses(db: Session = Depends(get_db), user: User = Depends(current_user)):
    return db.query(Expense).filter(Expense.owner_id == user.id).order_by(Expense.created_at.desc()).all()
@app.delete("/expenses/{exp_id}")
def delete_expense(exp_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
 exp = db.query(Expense).filter(Expense.id == exp_id, Expense.owner_id == user.id).first()
 if not exp:
     raise HTTPException(status_code=404, detail="Expense not found")
 db.delete(exp)
 db.commit()
 return {"ok": True}
@app.get("/expenses/summary")
def summary(db: Session = Depends(get_db), user: User = Depends(current_user)):
    rows = db.query(Expense.category, func.sum(Expense.amount)).filter(Expense.owner_id == user.id).group_by(Expense.category).all()
    return {"labels": [r[0] for r in rows], "data": [r[1] for r in rows]}
# ---------- Frontend ----------
INDEX_HTML = """
<!doctype html>
<html>
<head>
 <meta charset="utf-8"/>
 <title>Expense Tracker</title>
 <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
 <style>body{font-family:Arial;margin:20px} table{border-collapse:collapse;width:100%} th,td{border:1px solid #ccc;padding:6px}</style>
</head>
<body>
 <h1>Expense Tracker</h1>
 <section id="auth">
 <h3>Login</h3>
 <input id="email" placeholder="Email"> <input id="password" type="password" placeholder="Password">
 <button onclick="login()">Login</button>
 <p id="msg"></p>
 </section>
 <section id="app" style="display:none">
 <h3>Add Expense</h3>
 <input id="cat" placeholder="Category"> <input id="amt" type="number" placeholder="Amount"> <input id="com" placeholder="Comments">
 <button onclick="addExpense()">Add</button>
 <h3>Expenses</h3>
 <table><thead><tr><th>Category</th><th>Amount</th><th>Created</th><th>Updated</th><th>Comments</th><th>Action</th></tr></thead><tbody id="tbl"></tbody></table>
 <h3>Category-wise Pie Chart</h3>
 <canvas id="pie" width="400" height="400"></canvas>
 </section>
<script>
let token=null;
function api(path,opt={}){return fetch(path,{...opt,headers:{...(opt.headers||{}),...(token?{Authorization:`Bearer ${token}`}:{})}}).then(r=>r.json());}
function login(){
 fetch("/auth/login",{method:"POST",headers:{"Content-Type":"application/x-www-form-urlencoded"},body:new URLSearchParams({username:email.value,password:password.value})})
 .then(r=>r.json()).then(d=>{token=d.access_token;if(token){auth.style.display='none';app.style.display='block';load();}else msg.innerText='Login failed';});
}
function addExpense(){api("/expenses/?category="+cat.value+"&amount="+amt.value+"&comments="+com.value,{method:"POST"}).then(()=>load());}
function del(id){api("/expenses/"+id,{method:"DELETE"}).then(()=>load());}
function load(){api("/expenses/").then(xs=>{tbl.innerHTML='';xs.forEach(x=>{tbl.innerHTML+=`<tr><td>${x.category}</td><td>${x.amount}</td><td>${x.created_at}</td><td>${x.updated_at}</td><td>${x.comments||''}</td><td><button onclick=del(${x.id})>Delete</button></td></tr>`});pie();});}
let chart;function pie(){api("/expenses/summary").then(d=>{if(chart)chart.destroy();chart=new Chart(document.getElementById("pie"),{type:'pie',data:{labels:d.labels,datasets:[{data:d.data}]}});});}
</script>
</body>
</html>
"""
@app.get("/", response_class=HTMLResponse)
def home():
 return INDEX_HTML
if __name__ == "__main__":
 uvicorn.run("app:app", host="0.0.0.0", port=8000, reload=True)