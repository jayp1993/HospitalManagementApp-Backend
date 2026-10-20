import os
import pyodbc
import uvicorn
from fastapi import Body, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv

load_dotenv()
CONN = os.getenv("CONNECTION_STRING")  # Azure SQL ODBC connection string (see README)

app = FastAPI(title="Hospital Management API")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

SCHEMA = """
IF OBJECT_ID('Patients','U') IS NULL CREATE TABLE Patients(
  ID INT IDENTITY PRIMARY KEY, Name NVARCHAR(100) NOT NULL, Age INT, Gender NVARCHAR(10),
  Phone NVARCHAR(20), Address NVARCHAR(255), Disease NVARCHAR(150), AdmittedOn DATE DEFAULT GETDATE());
IF OBJECT_ID('Doctors','U') IS NULL CREATE TABLE Doctors(
  ID INT IDENTITY PRIMARY KEY, Name NVARCHAR(100) NOT NULL, Specialization NVARCHAR(100),
  Phone NVARCHAR(20), Email NVARCHAR(100), Available BIT DEFAULT 1);
IF OBJECT_ID('Appointments','U') IS NULL CREATE TABLE Appointments(
  ID INT IDENTITY PRIMARY KEY,
  PatientID INT NOT NULL REFERENCES Patients(ID) ON DELETE CASCADE,
  DoctorID INT NOT NULL REFERENCES Doctors(ID) ON DELETE CASCADE,
  AppointmentDate DATE NOT NULL, Status NVARCHAR(20) DEFAULT 'Scheduled', Notes NVARCHAR(500));
"""

# resource -> (table, writable columns, list query)
RESOURCES = {
    "patients": ("Patients", ["Name", "Age", "Gender", "Phone", "Address", "Disease"],
                 "SELECT * FROM Patients ORDER BY ID DESC"),
    "doctors": ("Doctors", ["Name", "Specialization", "Phone", "Email", "Available"],
                "SELECT * FROM Doctors ORDER BY ID DESC"),
    "appointments": ("Appointments", ["PatientID", "DoctorID", "AppointmentDate", "Status", "Notes"],
                     "SELECT a.*, p.Name AS PatientName, d.Name AS DoctorName FROM Appointments a "
                     "JOIN Patients p ON p.ID=a.PatientID JOIN Doctors d ON d.ID=a.DoctorID "
                     "ORDER BY a.AppointmentDate DESC"),
}


def run(sql, params=(), fetch=False):
    with pyodbc.connect(CONN) as conn:
        cur = conn.cursor()
        cur.execute(sql, params)
        if fetch:
            cols = [c[0] for c in cur.description]
            return [dict(zip(cols, r)) for r in cur.fetchall()]
        conn.commit()
        return cur.rowcount


def clean(cols, data):
    return [None if data.get(c) in ("", None) else data.get(c) for c in cols]


def register(name, table, cols, list_sql):
    @app.get(f"/api/{name}", name=f"list_{name}")
    def list_all():
        return run(list_sql, fetch=True)

    @app.post(f"/api/{name}", name=f"create_{name}")
    def create(data: dict = Body(...)):
        marks = ",".join("?" * len(cols))
        run(f"INSERT INTO {table} ({','.join(cols)}) VALUES ({marks})", clean(cols, data))
        return {"message": "Created"}

    @app.put(f"/api/{name}/{{item_id}}", name=f"update_{name}")
    def update(item_id: int, data: dict = Body(...)):
        sets = ",".join(f"{c}=?" for c in cols)
        if run(f"UPDATE {table} SET {sets} WHERE ID=?", clean(cols, data) + [item_id]) == 0:
            raise HTTPException(404, "Not found")
        return {"message": "Updated"}

    @app.delete(f"/api/{name}/{{item_id}}", name=f"delete_{name}")
    def delete(item_id: int):
        if run(f"DELETE FROM {table} WHERE ID=?", [item_id]) == 0:
            raise HTTPException(404, "Not found")
        return {"message": "Deleted"}


for _name, (_t, _c, _q) in RESOURCES.items():
    register(_name, _t, _c, _q)


@app.get("/api/stats")
def stats():
    return run("SELECT (SELECT COUNT(*) FROM Patients) AS patients, (SELECT COUNT(*) FROM Doctors) AS doctors, "
               "(SELECT COUNT(*) FROM Appointments) AS appointments, "
               "(SELECT COUNT(*) FROM Appointments WHERE AppointmentDate=CAST(GETDATE() AS DATE)) AS today",
               fetch=True)[0]


@app.get("/api")
def init_db():
    """Creates tables if they don't exist (also runs on startup)."""
    run(SCHEMA)
    return {"message": "Hospital API ready"}


@app.on_event("startup")
def startup():
    try:
        init_db()
    except Exception as e:  # keep API up so /api can be retried after fixing firewall/connection string
        print("DB init failed:", e)


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
