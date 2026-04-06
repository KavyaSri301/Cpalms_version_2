import json
from datetime import datetime, timezone
from db_pool import db_pool

MAX_HISTORY = 50


def _parse_json_column(value):
    """Safely parse a column that stores a JSON array. Returns a list."""
    if not value:
        return []
    try:
        parsed = json.loads(value)
        return parsed if isinstance(parsed, list) else [parsed]
    except (json.JSONDecodeError, TypeError):
        return [value]


def log_query_to_sql(resource_id, benchmarks, benchmarks_long, query, response, response_type,
                     session_id, user_id,
                     server, database, username, password, supporting_documents, worksheet=""):
    """
    Log chat query and response to SQL Server database.
    Uses the shared db_pool so background logging never opens fresh connections.
    benchmarks_long is accepted for backwards compatibility but always stored as empty string.
    """
    benchmarks_long = ""

    now_iso = datetime.now(timezone.utc).isoformat()
    supporting_documents_str = ','.join(supporting_documents) if supporting_documents else ""

    conn = db_pool.get_connection()
    cursor = None
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT query, response, Benchmarks, Benchmarks_Long,
                   Response_Type, Timestamp, supporting_documents, worksheet
            FROM ChatLogs WITH (UPDLOCK, HOLDLOCK)
            WHERE User_ID = ? AND Session_ID = ? AND resource_id = ?
            """,
            (user_id, session_id, resource_id)
        )
        existing = cursor.fetchone()

        if existing:
            queries        = _parse_json_column(existing.query)
            responses      = _parse_json_column(existing.response)
            benchmarks_arr = _parse_json_column(existing.Benchmarks)
            bench_long_arr = _parse_json_column(existing.Benchmarks_Long)
            resp_types     = _parse_json_column(existing.Response_Type)
            timestamps     = _parse_json_column(existing.Timestamp)
            supp_docs      = _parse_json_column(existing.supporting_documents)
            worksheets     = _parse_json_column(existing.worksheet)

            queries.append(query)
            responses.append(response)
            benchmarks_arr.append(benchmarks)
            bench_long_arr.append("")
            resp_types.append(response_type)
            timestamps.append(now_iso)
            supp_docs.append(supporting_documents_str)
            worksheets.append(worksheet if worksheet else "")

            queries = queries[-MAX_HISTORY:]
            responses = responses[-MAX_HISTORY:]
            benchmarks_arr = benchmarks_arr[-MAX_HISTORY:]
            bench_long_arr = bench_long_arr[-MAX_HISTORY:]
            resp_types = resp_types[-MAX_HISTORY:]
            timestamps = timestamps[-MAX_HISTORY:]
            supp_docs = supp_docs[-MAX_HISTORY:]
            worksheets = worksheets[-MAX_HISTORY:]

            cursor.execute(
                """
                UPDATE ChatLogs
                SET query                = ?,
                    response             = ?,
                    Benchmarks           = ?,
                    Benchmarks_Long      = ?,
                    Response_Type        = ?,
                    Timestamp            = ?,
                    supporting_documents = ?,
                    worksheet            = ?
                WHERE User_ID = ? AND Session_ID = ? AND resource_id = ?
                """,
                (
                    json.dumps(queries),
                    json.dumps(responses),
                    json.dumps(benchmarks_arr),
                    json.dumps(bench_long_arr),
                    json.dumps(resp_types),
                    json.dumps(timestamps),
                    json.dumps(supp_docs),
                    json.dumps(worksheets),
                    user_id, session_id, resource_id
                )
            )
            print(f"✅ SQL log updated — message #{len(queries)} appended for session {session_id}")

        else:
            cursor.execute(
                """
                INSERT INTO ChatLogs
                    (resource_id, User_ID, Session_ID,
                     query, response, Benchmarks, Benchmarks_Long,
                     Response_Type, Timestamp, supporting_documents, worksheet)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    resource_id, user_id, session_id,
                    json.dumps([query]),
                    json.dumps([response]),
                    json.dumps([benchmarks]),
                    json.dumps([""]),           
                    json.dumps([response_type]),
                    json.dumps([now_iso]),
                    json.dumps([supporting_documents_str]),
                    json.dumps([worksheet if worksheet else ""])
                )
            )
            print(f"✅ SQL log inserted — new conversation row for session {session_id}")

        conn.commit()

    except Exception as e:
        print("❌ SQL log failed:", e)
        try:
            conn.rollback()
        except Exception:
            pass
    finally:
        if cursor:
            try:
                cursor.close()
            except Exception:
                pass
        db_pool.return_connection(conn)