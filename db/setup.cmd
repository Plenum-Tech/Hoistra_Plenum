@echo off
REM Stand up a fresh Hoistra database and load the schema. Windows: cmd.exe or PowerShell.
REM
REM   db\setup.cmd
REM   db\setup.cmd my-db 5433
REM
REM Everything that needs a shell — waiting for postgres, looping over the files — happens
REM INSIDE the container, where bash exists. Nothing here depends on the shell you are
REM sitting in, which is the whole point: the bash version of this fails in cmd.exe with
REM "f was unexpected at this time", and the PowerShell version corrupts every non-ASCII
REM character in the reference data on its way through the pipe.

setlocal
set NAME=%~1
if "%NAME%"=="" set NAME=hoistra-db
set PORT=%~2
if "%PORT%"=="" set PORT=5432

REM pgvector, not plain postgres: the schema uses the vector extension for document
REM embeddings. On plain postgres that one statement fails and leaves a database that is
REM complete apart from document_chunks — worse than an obvious failure, because
REM everything else works.
set IMAGE=pgvector/pgvector:pg16

echo ==^> removing any container named %NAME%
docker rm -f %NAME% >nul 2>&1

echo ==^> starting %IMAGE% as %NAME% on port %PORT%
docker run -d --name %NAME% -e POSTGRES_USER=cafm -e POSTGRES_PASSWORD=cafm -e POSTGRES_DB=hoistra -p %PORT%:5432 %IMAGE% >nul
if errorlevel 1 (
  echo    FAILED to start the container. Is port %PORT% already in use?
  exit /b 1
)

echo ==^> copying the sql files into the container
docker cp "%~dp0." %NAME%:/db
if errorlevel 1 exit /b 1

echo ==^> waiting for postgres, then loading
REM The wait is not optional. "docker run -d" returns as soon as the container is CREATED,
REM not when postgres accepts connections — initdb takes about eight seconds on first
REM boot. Without this, every psql call fails and you are left with an empty database.
REM
REM ON_ERROR_STOP=1 is not optional either: psql exits 0 even when statements fail, so a
REM half-loaded database otherwise looks exactly like a good one.
docker exec %NAME% bash -c "until pg_isready -U cafm -d hoistra >/dev/null 2>&1; do sleep 1; done; for f in /db/0*.sql; do psql -q -U cafm -d hoistra -v ON_ERROR_STOP=1 -o /dev/null -f $f || exit 1; echo \"  loaded $(basename $f)\"; done"
if errorlevel 1 (
  echo    LOAD FAILED - the database is incomplete. Do not use it.
  exit /b 1
)

echo.
docker exec %NAME% psql -U cafm -d hoistra -tAc "SELECT (SELECT count(*) FROM information_schema.tables WHERE table_schema='plenum_cafm' AND table_type='BASE TABLE') || ' tables, ' || (SELECT count(*) FROM information_schema.columns WHERE table_schema='plenum_cafm') || ' columns, ' || (SELECT count(*) FROM information_schema.views WHERE table_schema='plenum_cafm') || ' views, ' || (SELECT count(*) FROM pg_indexes WHERE schemaname='plenum_cafm') || ' indexes'"

echo.
echo Ready. Point the service at it:
echo.
echo   DB_URL=postgresql+asyncpg://cafm:cafm@127.0.0.1:%PORT%/hoistra
echo   AUTH_DEFAULT_ORGANIZATION_ID=00000000-0000-0000-0000-000000000001
echo.
echo and set AUTH_JWT_SECRET and AUTH_OTP_PEPPER to 32+ random characters each.
endlocal
