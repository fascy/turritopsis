from gevent import monkey; monkey.patch_all(thread=False)

import json
import logging
import os
import traceback, time
import gevent
import numpy as np
from collections import namedtuple
from enum import Enum
from gevent import Greenlet
from gevent.queue import Queue
from speedydumbobft.core.speedydumbocommonsubset import speedydumbocommonsubset
from speedydumbobft.core.provablebroadcast import provablebroadcast
from speedydumbobft.core.validators import pb_validate
from dumbobft.core.speedmvbacommonsubset import speedmvbacommonsubset
# from honeybadgerbft.core.honeybadger_block import honeybadger_block
from honeybadgerbft.exceptions import UnknownTagError
from collections import defaultdict

def set_consensus_log(id: int):
    logger = logging.getLogger("consensus-node-"+str(id))
    logger.setLevel(logging.DEBUG)
    formatter = logging.Formatter(
        '%(asctime)s %(filename)s [line:%(lineno)d] %(funcName)s %(levelname)s %(message)s ')
    if 'log' not in os.listdir(os.getcwd()):
        os.mkdir(os.getcwd() + '/log')
    full_path = os.path.realpath(os.getcwd()) + '/log/' + "consensus-node-"+str(id) + ".log"
    file_handler = logging.FileHandler(full_path)
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
    return logger


class BroadcastTag(Enum):
    ACS_PRBC = 'ACS_PRBC'
    ACS_VACS = 'ACS_VACS'
    TPKE = 'TPKE'


BroadcastReceiverQueues = namedtuple(
    'BroadcastReceiverQueues', ('ACS_PRBC', 'ACS_VACS', 'TPKE'))


def broadcast_receiver_loop(recv_func, recv_queues):
    while True:
        #gevent.sleep(0)
        sender, (tag, j, msg) = recv_func()
        if tag not in BroadcastTag.__members__:
            # TODO Post python 3 port: Add exception chaining.
            # See https://www.python.org/dev/peps/pep-3134/
            raise UnknownTagError('Unknown tag: {}! Must be one of {}.'.format(
                tag, BroadcastTag.__members__.keys()))
        recv_queue = recv_queues._asdict()[tag]

        if tag == BroadcastTag.ACS_PRBC.value:
            recv_queue = recv_queue[j]
        try:
            recv_queue.put_nowait((sender, msg))
        except AttributeError as e:
            print("error", sender, (tag, j, msg))
            traceback.print_exc(e)


class SpeedyDumbo():
    """Dumbo object used to run the protocol.

    :param str sid: The base name of the common coin that will be used to
        derive a nonce to uniquely identify the coin.
    :param int pid: Node id.
    :param int B: Batch size of transactions.
    :param int N: Number of nodes in the network.
    :param int f: Number of faulty nodes that can be tolerated.
    :param TBLSPublicKey sPK: Public key of the (f, N) threshold signature
        (:math:`\mathsf{TSIG}`) scheme.
    :param TBLSPrivateKey sSK: Signing key of the (f, N) threshold signature
        (:math:`\mathsf{TSIG}`) scheme.
    :param TBLSPublicKey sPK1: Public key of the (N-f, N) threshold signature
        (:math:`\mathsf{TSIG}`) scheme.
    :param TBLSPrivateKey sSK1: Signing key of the (N-f, N) threshold signature
        (:math:`\mathsf{TSIG}`) scheme.
    :param list sPK2s: Public key(s) of ECDSA signature for all N parties.
    :param PrivateKey sSK2: Signing key of ECDSA signature.
    :param str ePK: Public key of the threshold encryption
        (:math:`\mathsf{TPKE}`) scheme.
    :param str eSK: Signing key of the threshold encryption
        (:math:`\mathsf{TPKE}`) scheme.
    :param send:
    :param recv:
    :param K: a test parameter to specify break out after K rounds
    """

    def __init__(self, sid, pid, B, N, f, sPK2s, sSK2, send, recv, K=3, mute=False, debug=False):
        self.sid = sid
        self.id = pid
        self.B = B
        self.N = N
        self.f = f
        # self.sPK = sPK
        # self.sSK = sSK
        # self.sPK1 = sPK1
        # self.sSK1 = sSK1
        self.sPK2s = sPK2s
        self.sSK2 = sSK2
        self._send = send
        self._recv = recv
        self.logger = set_consensus_log(pid)
        self.round = 0  # Current block number
        self.transaction_buffer = Queue()
        self._per_round_recv = {}  # Buffer of incoming messages
        self.salvagset = defaultdict(lambda:defaultdict())
        self.pb_value_outputs = defaultdict(lambda :[Queue(1) for _ in range(self.N)])
        self.pb_proof_output = defaultdict(lambda :Queue(1))
        self.last_pb_proof = defaultdict()
        self.K = K

        self.s_time = 0
        self.e_time = 0
        self.txcnt = 0

        self.mute = mute
        self.debug = debug

    def submit_tx(self, tx):
        """Appends the given transaction to the transaction buffer.
        :param tx: Transaction to append to the buffer.
        """
        #print('backlog_tx', self.id, tx)
        #if self.logger != None:
        #    self.logger.info('Backlogged tx at Node %d:' % self.id + str(tx))
        # Insert transactions to the end of TX buffer
        self.transaction_buffer.put_nowait(tx)

    def run_bft(self):
        """Run the Dumbo protocol."""

        def _recv_loop():
            """Receive messages."""
            #print("start recv loop...")
            while True:
                #gevent.sleep(0)
                try:
                    (sender, (r, msg) ) = self._recv()
                    #self.logger.info('recv1' + str((sender, o)))
                    #print('recv1' + str((sender, o)))
                    # Maintain an *unbounded* recv queue for each epoch
                    if r not in self._per_round_recv:
                        self._per_round_recv[r] = Queue()
                    # Buffer this message
                    self._per_round_recv[r].put_nowait((sender, msg))
                except:
                    continue

        #self._recv_thread = gevent.spawn(_recv_loop)
        self._recv_thread = Greenlet(_recv_loop)
        self._recv_thread.start()

        self.s_time = time.time()
        if self.logger != None:
            self.logger.info('Node %d starts to run at time:' % self.id + str(self.s_time))

        while True:

            # For each round...
            #gevent.sleep(0)

            start = time.time()

            r = self.round
            if r not in self._per_round_recv:
                self._per_round_recv[r] = Queue()

            # Select B transactions (TODO: actual random selection)
            tx_to_send = []
            for _ in range(self.B):
                tx_to_send.append(self.transaction_buffer.get_nowait())

            def _make_send(r):
                def _send(j, o):
                    self._send(j, (r, o))
                return _send

            send_r = _make_send(r)
            recv_r = self._per_round_recv[r].get
            new_tx = self._run_round(r, tx_to_send, send_r, recv_r)

            if self.logger != None:
                tx_cnt = str(new_tx).count("Dummy TX")
                # self.txcnt += tx_cnt
                self.logger.info('Node %d Delivers ACS Block in Round %d with having %d TXs' % (self.id, r, tx_cnt))

            end = time.time()

            if self.logger != None:
                self.logger.info('ACS Block Delay at Node %d: ' % self.id + str(end - start))


            # return self.round  # Only run one round for now
            self.round += 1     # Increment the round
            if self.round > self.K:
                self.e_time = time.time()
                if self.logger != None:
                    self.logger.info("node %d breaks in %f seconds with total delivered Txs %d" % (
                        self.id, self.e_time - self.s_time, self.txcnt))
                print("node %d breaks in %f seconds with total delivered Txs %d, latency: %f, tps: %f" % (
                    self.id, self.e_time - self.s_time, self.txcnt,
                    (self.e_time - self.s_time) / self.round,
                    self.txcnt / (self.e_time - self.s_time)))
                # gevent.sleep(0.5)
                break   # Only run one round for now

        if self.logger != None:
            self.e_time = time.time()
            self.logger.info("node %d breaks in %f seconds with total delivered Txs %d" % (self.id, self.e_time-self.s_time, self.txcnt))
        else:
            print("node %d breaks" % self.id)

        #self._recv_thread.join(timeout=2)

    #
    def _run_round(self, r, tx_to_send, send, recv):
        """Run one protocol round.
        :param int r: round id
        :param tx_to_send: Transaction(s) to process.
        :param send:
        :param recv:
        """

        # Unique sid for each round
        sid = self.sid + ':' + str(r)
        pid = self.id
        N = self.N
        f = self.f

        pb_recvs = [Queue() for _ in range(N)]
        vacs_recv = Queue()
        tpke_recv = Queue()

        my_pb_input = Queue(1)

        pb_value_outputs = [Queue(1) for _ in range(N)]
        pb_proof_output = Queue(1)
        pb_proofs = dict()

        vacs_input = Queue(1)
        vacs_output = Queue(1)
        left_output = Queue(1)
        s_t = time.time()
        recv_queues = BroadcastReceiverQueues(
            ACS_PRBC=pb_recvs,
            ACS_VACS=vacs_recv,
            TPKE=tpke_recv,
        )

        bc_recv_loop_thread = Greenlet(broadcast_receiver_loop, recv, recv_queues)
        bc_recv_loop_thread.start()

        #print(pid, r, 'tx_to_send:', tx_to_send)
        #if self.logger != None:
        #    self.logger.info('Commit tx at Node %d:' % self.id + str(tx_to_send))

        pb_threads = [None] * N

        def _setup_pb(j):
            """Setup the sub protocols RBC, BA and common coin.
            :param int j: Node index for which the setup is being done.
            """

            def pb_send(k, o):
                """Reliable send operation.
                :param k: Node to send.
                :param o: Value to send.
                """
                send(k, ('ACS_PRBC', j, o))

            # Only leader gets input
            pb_input = my_pb_input.get if j == pid else None

            pb_thread = gevent.spawn(provablebroadcast, sid+'PB'+str(r)+str(j), pid,
                                     N, f, self.sPK2s, self.sSK2, j, pb_input,
                                     self.pb_value_outputs[r][j].put_nowait,
                                     recv=pb_recvs[j].get, send=pb_send, logger=None)

            def wait_for_pb_proof():
                proof = pb_thread.get()
                try:
                    pb_proofs[sid+'PB'+str(r)+str(j)] = proof
                    pb_proof_output.put_nowait(proof)
                except TypeError as e:
                    print(e)
                    #return False
            # wait for pb proof, only when I am the leader
            if j == pid:
                gevent.spawn(wait_for_pb_proof)

            return pb_thread

        # N instances of PB
        for j in range(N):
            #print("start to set up RBC %d" % j)
            pb_threads[j] = _setup_pb(j)



        # One instance of (validated) ACS
        #print("start to set up VACS")
        def vacs_send(k, o):
            """Threshold encryption broadcast."""
            """Threshold encryption broadcast."""
            send(k, ('ACS_VACS', '', o))

        def vacs_predicate(j, vj, t):
            if t == 1:
                prbc_sid = sid + 'PB' + str(r) + str(j)
            else:
                prbc_sid = self.sid + ':' + str(r-1) + 'PB' + str(r-1)+ str(j)
            # prbc_sid = sid + 'PB' + str(r) + str(j)
            try:
                proof = vj
                if prbc_sid in pb_proofs.keys():
                    try:
                        _prbc_sid, _digest, _sigmas = proof
                        assert prbc_sid == _prbc_sid
                        _, digest, _ = pb_proofs[prbc_sid]
                        assert digest == _digest
                        return True
                    except AssertionError:
                        print("1 Failed to verify proof for RBC")
                        return False
                assert pb_validate(prbc_sid, N, f, self.sPK2s, proof)
                pb_proofs[prbc_sid] = proof
                return True
            except AssertionError:
                print("2 Failed to verify proof for RBC")
                return False

        vacs_thread = Greenlet(speedmvbacommonsubset, sid + 'VACS' + str(r), pid, N, f,self.last_pb_proof,
                               self.sPK2s, self.sSK2,
                               vacs_input.get, vacs_output.put_nowait, left_output.put_nowait,
                               vacs_recv.get, vacs_send, vacs_predicate, logger=self.logger)
        vacs_thread.start()

        # One instance of TPKE
        def tpke_bcast(o):
            """Threshold encryption broadcast."""
            send(-1, ('TPKE', '', o))

        # One instance of ACS pid, N, f, prbc_out, vacs_in, vacs_out
        if r!=0:
            dumboacs_thread = Greenlet(speedydumbocommonsubset, pid, N, f, self.salvagset[r-1],
                           [_.get for _ in self.pb_value_outputs[r]], [_.get for _ in self.pb_value_outputs[r-1]],
                           pb_proof_output.get,
                           vacs_input.put_nowait,
                           vacs_output.get)
        else:
            dumboacs_thread = Greenlet(speedydumbocommonsubset, pid, N, f, self.salvagset[r-1],
                           [_.get for _ in self.pb_value_outputs[r]], None,
                           pb_proof_output.get,
                           vacs_input.put_nowait,
                           vacs_output.get)

        dumboacs_thread.start()
        """
        _output = honeybadger_block(pid, self.N, self.f, self.ePK, self.eSK,
                          propose=json.dumps(tx_to_send),
                          acs_put_in=my_pb_input.put_nowait, acs_get_out=dumboacs_thread.get,
                          tpke_bcast=tpke_bcast, tpke_recv=tpke_recv.get,logger=self.logger)

        """
        my_pb_input.put_nowait(json.dumps(tx_to_send))
        acs_get_out, pb_left, count_t = dumboacs_thread.get()
        left_vector = left_output.get()
        self.salvagset[r] = pb_left
        self.last_pb_proof = left_vector
        if r >= 2:
            try:
                self.salvagset.pop(r-2)
                self.pb_value_outputs.pop(r-2)
                self.pb_proof_output.pop(r-2)
            except Exception as e:
                print((self.id, r))
                pass

        count = 0
        decryptions = []
        for i, v in enumerate(acs_get_out):
            if v is None:
                continue
            count += 1
            # print(type(v))
            # plain = pickle.loads(v)
            decryptions.append(v)
        # if self.id == 4: print("decryptions", decryptions)
        block = set()
        mem_set = set()
        for batch in tuple(decryptions):
            decoded_batch = json.loads(batch)
            for tx in decoded_batch:
                block.add(tx)
                if tx.find('Membership') >= 0:
                    mem_set.add(tx)

        new_tx = list(block)
        # if self.id == 4: print(new_tx)
        new_mem = list(mem_set)
        tx_cnt = str(new_tx).count("Dummy TX")
        if pid == 0: print(r, "tx_cnt", tx_cnt)
        tx_cnt += count_t * self.B
        if pid == 0: print(r, "count", count_t * self.B)
        mem_cnt = str(new_tx).count("Membership TX")
        if pid == 0: print(r, "mem", mem_cnt)
        self.txcnt += tx_cnt + mem_cnt
        print("round,", r , "in total:", self.txcnt)
        e_time = time.time()
        if self.logger != None:
            self.logger.info(
                'Node %d Delivers ACS Block in Round %d with having %d TXs' % (self.id, r, tx_cnt + mem_cnt))
        print("Node", self.id, "Delivers ACS Block in Round", r, "with having", tx_cnt + mem_cnt, "TXs")
        tps = self.txcnt / (e_time - self.s_time)
        latency = (e_time - self.s_time) / (self.round+1)
        if self.logger != None:
            self.logger.info(
                'Node %d counting so-far tps: %f, latency %f' % (self.id, tps, latency))
        print('Node %d counting so-far tps: %f, latency %f' % (self.id, tps, latency))

        dumboacs_thread.kill()
        bc_recv_loop_thread.kill()
        vacs_thread.kill()
        for j in range(N):
            pb_threads[j].kill()

        return list(block)

    # TODO： make help and callhelp threads to handle the rare cases when vacs (vaba) returns None