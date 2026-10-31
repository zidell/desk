"""Protocol/lifecycle tests without reading or controlling the desktop."""
import sys
import unittest
from accessibility_worker import AccessibilityWorker

FIXTURE=r'''
import sys,json,time
count=0
for line in sys.stdin:
 r=json.loads(line);count+=1;mode=r['window'].get('mode')
 if mode=='hang':time.sleep(10)
 if mode=='exit':sys.exit(1)
 print(json.dumps({'request_id':-1 if mode=='stale' else r['request_id'],
  'bindings':[{'fresh':count}], 'warnings':[]}),flush=True)
'''

class WorkerTests(unittest.TestCase):
    def setUp(self):self.worker=AccessibilityWorker([sys.executable,'-u','-c',FIXTURE])
    def tearDown(self):self.worker.close()
    def test_reuses_process_and_reads_fresh_results(self):
        self.assertEqual(self.worker.request({})['bindings'],[{'fresh':1}])
        pid=self.worker.process.pid
        for i in range(2,10001):
            self.assertEqual(self.worker.request({})['bindings'],[{'fresh':i}])
            self.assertEqual(self.worker.process.pid,pid)
    def test_hang_closes_worker_and_backs_off(self):
        with self.assertRaises(TimeoutError):self.worker.request({'mode':'hang'},timeout=.03)
        self.assertIsNone(self.worker.process)
        with self.assertRaises(TimeoutError):self.worker.request({})
        self.assertIsNone(self.worker.process)
        self.worker.retry_at=0
        self.assertEqual(self.worker.request({})['bindings'],[{'fresh':1}])
    def test_exit_backs_off(self):
        with self.assertRaises(EOFError):self.worker.request({'mode':'exit'})
        with self.assertRaises(TimeoutError):self.worker.request({})
    def test_stale_reply_is_refused(self):
        with self.assertRaises(ValueError):self.worker.request({'mode':'stale'})
        self.assertIsNone(self.worker.process)
    def test_stop_during_wait_closes_worker(self):
        class Stop(Exception):pass
        def pump(_):raise Stop()
        with self.assertRaises(Stop):self.worker.request({'mode':'hang'},pump=pump)
        self.assertIsNone(self.worker.process)
    def test_close_is_clean_and_idempotent(self):
        self.worker.request({});process=self.worker.process
        self.worker.close();self.worker.close()
        self.assertEqual(process.returncode,0)

if __name__=='__main__':unittest.main()
