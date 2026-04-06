"""
Database connection pooling for improved concurrency
"""
import pyodbc
import threading
from queue import Queue, Empty
from config import AZURE_SQL_CONNECTION
import logging
import time

logger = logging.getLogger('db_pool')

class ConnectionPool:
    """Thread-safe database connection pool"""

    def __init__(self, connection_string: str, pool_size: int = 10):
        self.connection_string = connection_string
        self.pool_size = pool_size
        self.pool = Queue(maxsize=pool_size)
        self.lock = threading.Lock()
        self.stats = {
            'total_gets': 0,
            'total_returns': 0,
            'pool_exhausted_count': 0,
            'connection_errors': 0,
            'dead_connections': 0
        }
        self._initialize_pool()
        logger.info(f"✅ Connection pool initialized with {pool_size} connections")

    def _initialize_pool(self):
        """Initialize the connection pool"""
        logger.info(f"Initializing connection pool with {self.pool_size} connections...")
        for i in range(self.pool_size):
            try:
                start = time.time()
                conn = pyodbc.connect(self.connection_string, timeout=10)
                duration = (time.time() - start) * 1000
                self.pool.put(conn)
                logger.debug(f"Connection {i+1}/{self.pool_size} created in {duration:.2f}ms")
            except Exception as e:
                logger.error(f"Error creating connection {i+1}: {e}")
                self.stats['connection_errors'] += 1

    def get_connection(self, timeout: int = 5):
        """Get a connection from the pool"""
        thread_id = threading.current_thread().ident
        start_time = time.time()

        with self.lock:
            self.stats['total_gets'] += 1
            current_size = self.pool.qsize()

        logger.debug(
            f"Thread-{thread_id} requesting connection | "
            f"Pool size: {current_size}/{self.pool_size}"
        )

        try:
            conn = self.pool.get(timeout=timeout)
            wait_time = (time.time() - start_time) * 1000

            try:
                test_start = time.time()
                _c = conn.cursor()
                _c.execute("SELECT 1")
                _c.close()
                test_duration = (time.time() - test_start) * 1000

                logger.debug(
                    f"Thread-{thread_id} got connection | "
                    f"Wait: {wait_time:.2f}ms | Test: {test_duration:.2f}ms"
                )
                return conn
            except Exception as e:
                logger.warning(
                    f"Thread-{thread_id} found dead connection, creating new one | Error: {e}"
                )
                with self.lock:
                    self.stats['dead_connections'] += 1

                conn = pyodbc.connect(self.connection_string, timeout=10)
                return conn

        except Empty:
            wait_time = (time.time() - start_time) * 1000

            with self.lock:
                self.stats['pool_exhausted_count'] += 1

            logger.warning(
                f"⚠️ Thread-{thread_id} | Connection pool EXHAUSTED | "
                f"Wait: {wait_time:.2f}ms | Creating temporary connection | "
                f"Total exhausted: {self.stats['pool_exhausted_count']}"
            )

            return pyodbc.connect(self.connection_string, timeout=10)

    def return_connection(self, conn):
        """Return a connection to the pool"""
        thread_id = threading.current_thread().ident

        with self.lock:
            self.stats['total_returns'] += 1
            current_size = self.pool.qsize()

        try:
            if current_size < self.pool_size:
                self.pool.put_nowait(conn)
                logger.debug(
                    f"Thread-{thread_id} returned connection | "
                    f"Pool size: {current_size + 1}/{self.pool_size}"
                )
            else:
                conn.close()
                logger.debug(
                    f"Thread-{thread_id} closed connection (pool full) | "
                    f"Pool size: {current_size}/{self.pool_size}"
                )
        except Exception as e:
            logger.error(f"Thread-{thread_id} error returning connection: {e}")
            try:
                conn.close()
            except:
                pass

    def get_stats(self):
        """Get pool statistics"""
        with self.lock:
            return {
                **self.stats,
                'current_pool_size': self.pool.qsize(),
                'max_pool_size': self.pool_size,
                'pool_utilization': f"{(1 - self.pool.qsize() / self.pool_size) * 100:.1f}%"
            }

    def log_stats(self):
        """Log current pool statistics"""
        stats = self.get_stats()
        logger.info(
            f"Pool Stats | "
            f"Size: {stats['current_pool_size']}/{stats['max_pool_size']} | "
            f"Utilization: {stats['pool_utilization']} | "
            f"Gets: {stats['total_gets']} | "
            f"Returns: {stats['total_returns']} | "
            f"Exhausted: {stats['pool_exhausted_count']} | "
            f"Dead: {stats['dead_connections']}"
        )

    def close_all(self):
        """Close all connections in the pool"""
        logger.info("Closing all connections in pool...")
        closed_count = 0
        while not self.pool.empty():
            try:
                conn = self.pool.get_nowait()
                conn.close()
                closed_count += 1
            except:
                pass
        logger.info(f"Closed {closed_count} connections")

db_pool = ConnectionPool(AZURE_SQL_CONNECTION, pool_size=10)

def log_pool_stats_periodically():
    """Background thread to log pool stats every 30 seconds"""
    import time
    while True:
        time.sleep(30)
        db_pool.log_stats()

def keepalive_connections():
    """Ping idle pooled connections every 4 minutes to prevent Azure SQL from dropping them."""
    while True:
        time.sleep(240)
        refreshed = 0
        temp_conns = []
        while not db_pool.pool.empty():
            try:
                conn = db_pool.pool.get_nowait()
                temp_conns.append(conn)
            except Exception:
                break
        for conn in temp_conns:
            try:
                c = conn.cursor()
                c.execute("SELECT 1")
                c.close()
                db_pool.pool.put_nowait(conn)
                refreshed += 1
            except Exception:
                try:
                    conn.close()
                except Exception:
                    pass
                try:
                    new_conn = pyodbc.connect(db_pool.connection_string, timeout=10)
                    db_pool.pool.put_nowait(new_conn)
                    refreshed += 1
                except Exception as e:
                    logger.error(f"Keepalive: failed to replace dead connection: {e}")
        logger.debug(f"Keepalive: refreshed {refreshed} connections")

stats_thread = threading.Thread(target=log_pool_stats_periodically, daemon=True)
stats_thread.start()

keepalive_thread = threading.Thread(target=keepalive_connections, daemon=True)
keepalive_thread.start()
