from gevent import monkey;

monkey.patch_all(thread=False)
import logging
import os
from speedmvba_dy.core.spbc_ec_dy import strongprovablebroadcast
from adkr.keyrefersh.core.thresholdcoin import thresholdcoin
from adkr.keyrefersh.core.thresholdcoin_bn import thresholdcoin_bn
import hashlib
import pickle
import time
import traceback
import gevent
from collections import namedtuple
from gevent import Greenlet
from gevent.event import Event
from enum import Enum
from collections import defaultdict
from gevent.queue import Queue
from crypto.ecdsa.ecdsa import ecdsa_vrfy, ecdsa_sign
from honeybadgerbft.exceptions import UnknownTagError


class MessageTag(Enum):
    MVBA_SPBC = 'MVBA_SPBC'  # [Queue()] * N
    MVBA_ELECT = 'MVBA_ELECT'  #
    MVBA_ABA = 'MVBA_ABA'  # [Queue()] * Number_of_ABA_Iterations
    MVBA_HALT = 'MVBA_HALT'
    MVBA_DUM = 'MVBA_DUM'


MessageReceiverQueues = namedtuple(
    'MessageReceiverQueues', ('MVBA_SPBC', 'MVBA_ELECT', 'MVBA_ABA', 'MVBA_HALT', 'MVBA_DUM'))


def hash(x):
    return hashlib.sha256(pickle.dumps(x)).digest()


def recv_loop(pid, recv_func, recv_queues, C, logger):
    while True:
        sender, (tag, r, j, msg) = recv_func()
        # if j == 7:
        # print(pid, "recv2", (sender, (tag, j, msg[0])))
        # if logger != None:
        #     logger.info('recv from %d %s msg of [%d]-%s' % (sender, tag, j, msg[0]))
        if tag not in MessageTag.__members__:
            raise UnknownTagError('Unknown tag: {}! Must be one of {}.'.format(
                tag, MessageTag.__members__.keys()))
        recv_queue = recv_queues._asdict()[tag]
        if tag in {MessageTag.MVBA_SPBC.value}:
            recv_queue = recv_queue[r][C.index(j)]
        elif tag in {MessageTag.MVBA_ELECT.value, MessageTag.MVBA_DUM.value}:
            recv_queue = recv_queue
        elif tag in {MessageTag.MVBA_HALT.value}:
            # if pid == 3: print("-------------------------------- Receive a HALT msg from %d" % sender)
            recv_queue = recv_queue
        else:
            recv_queue = recv_queue[r]
        try:
            recv_queue.put((sender, msg))
            # if tag in {MessageTag.MVBA_HALT.value}:
            #     if pid == 3: print("-------------------------------- HALT msg from %d is placed in the queue" % sender)
        except Exception as e:
            # print((sender, msg))
            traceback.print_exc(e)
        gevent.sleep(0)


def speedmvba(sid, pid, N, f, l, C, PK2s, SK2, epks, esk, g, ty, input, decide, receive, send, predicate, logger=None):
    """Multi-valued Byzantine consensus. It takes an input ``vi`` and will
    finally writes the decided value into ``decide`` channel.
    :param sid: session identifier
    :param pid: my id number
    :param N: the number of parties
    :param f: the number of byzantine parties
    :param list PK2s: an array of ``coincurve.PublicKey'', i.e., N public keys of ECDSA for all parties
    :param PublicKey SK2: ``coincurve.PrivateKey'', i.e., secret key of ECDSA
    :param input: ``input()`` is called to receive an input
    :param decide: ``decide()`` is eventually called
    :param receive: receive channel
    :param send: send channel
    :param predicate: ``predicate()`` represents the externally validated condition
    """

    hasOutputed = False
    s_t = time.time()
    # print("Starts to run validated agreement...")

    """ 
    """
    """ 
    Some instantiations
    """
    """ 
    """

    r = 0

    my_spbc_input = Queue(1)

    halt_send = Queue()

    vote_recvs = defaultdict(lambda: Queue())
    aba_recvs = defaultdict(lambda: Queue())

    spbc_recvs = defaultdict(lambda: [Queue() for _ in range(N)])
    coin_recv = Queue()
    halt_recv = Queue()

    spbc_threads = [None] * N
    spbc_outputs = [Queue(1) for _ in range(N)]
    spbc_s1_list = [Queue(1) for _ in range(N)]
    s1_list = [Queue(1) for _ in range(N)]

    is_spbc_delivered = [0] * N
    is_s1_delivered = [0] * N

    # Leaders = [Queue(1) for _ in range(50)]
    Leaders = defaultdict(lambda: Queue(1))
    recv_queues = MessageReceiverQueues(
        MVBA_SPBC=spbc_recvs,
        MVBA_ELECT=coin_recv,
        MVBA_ABA=aba_recvs,
        MVBA_HALT=halt_recv,
        MVBA_DUM=Queue()
    )

    okay_to_stop = Event()
    okay_to_stop.clear()

    start_wait_for_halt = Event()
    start_wait_for_halt.clear()

    def broadcast(o):
        for i in C:
            send(i, o)
        # send(-1, o)

    recv_loop_thred = Greenlet(recv_loop, pid, receive, recv_queues, C, logger)
    recv_loop_thred.start()

    def views():
        nonlocal hasOutputed, r

        def spbc_pridict(m):
            # print("------", m)
            msg, proof, round, tag = m

            # both yes and no vote
            if round == 0:
                return 3
            L = Leaders[round].get()
            if tag == 'yn':
                hash_e = hash(str((sid + 'SPBC' + str(L), msg, "ECHO")))
                try:
                    for (k, sig_k) in proof:
                        assert ecdsa_vrfy(PK2s[k], hash_e, sig_k)
                except AssertionError:
                    # if logger is not None: logger.info("sig L verify failed!")
                    print("sig L verify failed!")
                    return -1
                return 1
            if tag == 'no':
                digest_no_no = hash(str((sid, L, r - 1, 'vote')))
                try:
                    for (k, sig_nono) in proof:
                        assert ecdsa_vrfy(PK2s[k], digest_no_no, sig_nono)
                except AssertionError:
                    # if logger is not None: logger.info("sig nono verify failed!")
                    print("sig nono verify failed!")
                    return -2
                return 2

        while not start_wait_for_halt.is_set():
            # if pid == 1:
            #     print("==========start round", r, " spbc =====================")
            """ 
            Setup the sub protocols Input Broadcast SPBCs"""

            for j in range(N):
                def make_spbc_send(j, r):  # this make will automatically deep copy the enclosed send func
                    def spbc_send(k, o):
                        """SPBC send operation.
                        :param k: Node to send.
                        :param o: Value to send.
                        """
                        # print("node", pid, "is sending", o[0], "to node", k, "with the leader", j)
                        send(k, ('MVBA_SPBC', r, j, o))

                    return spbc_send

                # Only leader gets input

                if C[j] == pid:
                    spbc_input = my_spbc_input.get
                else:
                    spbc_input = None
                spbc = gevent.spawn(strongprovablebroadcast, sid + 'SPBC' + str(C[j]), pid, N, f, l, C, PK2s, SK2, C[j],
                                    spbc_input, spbc_s1_list[j].put_nowait, spbc_recvs[r][j].get,
                                    make_spbc_send(C[j], r),
                                    r, logger, spbc_pridict)

                spbc_threads[j] = spbc

            """ 
            Setup the sub protocols permutation coins"""

            def coin_bcast(o):
                """Common coin multicast operation.
                :param o: Value to multicast.
                """
                broadcast(('MVBA_ELECT', r, 'leader_election', o))

            # permutation_coin = shared_coin(sid + 'PERMUTE', pid, N, f,
            #                               PK, SK, coin_bcast, coin_recv.get, single_bit=False)

            # print(pid, "coin share start")
            # False means to get a coin of 256 bits instead of a single bit

            """ 
            """
            """ 
            Start to run consensus
            """
            """ 
            """

            """ 
            Run n SPBC instance to consistently broadcast input values
            """

            # cbc_values = [Queue(1) for _ in range(N)]
            def wait_for_input():
                global my_msg
                v = input()
                my_msg = v

                my_spbc_input.put_nowait((v, "null", 0, "first"))
                # print(v[0])

            if r == 0:
                gevent.spawn(wait_for_input)

            def get_spbc_s1(leader):
                cl = C.index(leader)
                sid, pid, msg, sigmas1 = spbc_s1_list[cl].get()
                # print(sid, pid, "finish pcbc in round", r)
                if s1_list[cl].empty() is not True:
                    s1_list[cl].get()

                s1_list[cl].put_nowait((msg, sigmas1))
                is_s1_delivered[cl] = 1

            spbc_s1_threads = [gevent.spawn(get_spbc_s1, C[node]) for node in range(N)]

            wait_spbc_signal = Event()
            wait_spbc_signal.clear()

            def wait_for_spbc_to_continue(leader):
                # Receive output from CBC broadcast for input values
                try:
                    cl = C.index(leader)
                    # print("?")
                    msg, sigmas2 = spbc_threads[cl].get()
                    # print(pid, " in spbc[", leader, "] finished")
                    # print("=====predicate", predicate)
                    if predicate(msg[0]):
                        # print(pid, "predicate true")
                        # print(pid, " =======in spbc[", leader, "] finished")
                        try:
                            if spbc_outputs[cl].empty() is not True:
                                spbc_outputs[cl].get()
                            spbc_outputs[cl].put_nowait((msg, sigmas2))
                            is_spbc_delivered[cl] = 1
                            # print(is_spbc_delivered)
                            # if r == 0:
                            #     if sum(is_spbc_delivered) >= N - f - l:
                            #         wait_spbc_signal.set()
                            # else:
                            if sum(is_spbc_delivered) >= N - f - l:
                                # print(sid, pid, "receive n-f-l spbc in round", r, is_spbc_delivered)
                                wait_spbc_signal.set()
                        except Exception as e:
                            traceback.print_exc(e)
                            pass
                    else:
                        print(pid, "predicate false")
                        pass
                    gevent.sleep(0)
                except Exception as e:
                    print(e)
                    pass

            spbc_out_threads = [gevent.spawn(wait_for_spbc_to_continue, C[node]) for node in range(N)]

            wait_spbc_signal.wait()

            """
            Run a Coin instance to elect the leaders
            """
            # gevent.sleep(0)
            time.sleep(0.05)
            seed = int.from_bytes(hash(sid + str(r)), byteorder='big') % (2 ** 10 - 1)
            if r < 5:
                leader_index = seed % N
            # print(pid, ": round", r, "leader index:", leader_index)
            else:
                if ty == 's':
                    coin = thresholdcoin(sid + 'PERMUTE', pid, N, f, l, C, g, seed, epks, esk,  coin_recv.get, coin_bcast)
                elif ty == 'b':
                    coin = thresholdcoin_bn(sid + 'PERMUTE', pid, N, f, l, C, g, seed, epks, esk,  coin_recv.get, coin_bcast)
                leader_index = coin % N
                        # seed = permutation_coin('permutation')  # Block to get a random seed to permute the list of nodes

            # print(pid, ": round", r, "leader index:", leader_index)

            # print("coin has a seed:", seed)
            if logger != None:
                logger.info('%d round leader index %d' % (r, C[leader_index]))
            # print(pid, ": round", r, "leader index:", leader_index)
            Leader = C[leader_index]
            Leaders[r].put(Leader)
            if is_spbc_delivered[leader_index] == 1:
                msg, s2 = spbc_outputs[leader_index].get()
                halt_msg = (Leader, 2, msg, s2)
                # broadcast(('MVBA_HALT', r, pid, ("halt", halt)))
                halt_send.put_nowait(('MVBA_HALT', r, pid, ("halt", halt_msg)))
                if logger is not None:
                    logger.info("round %d smvba decide in shortcut. %f" % (r, time.time()))
                # print(leader_index, "has received, halt here")
                hasOutputed = True
                okay_to_stop.set()
                start_wait_for_halt.set()
                # except:
                #    print("2 can not")
                #    pass
                return 2
            if is_s1_delivered[leader_index] == 1:
                msg, s1 = s1_list[leader_index].queue[0]
                prevote = (Leader, 1, msg, s1)
                # print(pid, sid, "prevote in round ", r)
            else:
                digest_no = hash(str((sid, Leader, r, 'pre')))
                # digest_no = PK1.hash_message(str((sid, Leader, r, 'pre')))
                prevote = (Leader, 0, "bottom", ecdsa_sign(SK2, digest_no))
                # prevote = (Leader, 0, "bottom", SK1.sign(digest_no))
                # if pid == 1: print(pid, sid, "prevote no in round ", r)
            broadcast(('MVBA_ABA', r, r, ('prevote', prevote)))

            prevote_no_shares = dict()
            vote_yes_shares = dict()
            vote_no_shares = dict()

            okay_to_stop.clear()

            def vote_loop():

                nonlocal hasOutputed, r

                hasVoted = False
                while not hasOutputed and not okay_to_stop.is_set() and not start_wait_for_halt.is_set():
                    # gevent.sleep(0)
                    # hasOutputed = False
                    try:
                        # gevent.sleep(0.001)
                        sender, aba_msg = aba_recvs[r].get(0.001)
                        aba_tag, vote_msg = aba_msg
                        if aba_tag == 'prevote' and not hasVoted:
                            digest_no = hash(str((sid, Leader, r, 'pre')))
                            vote_yes_msg = 0
                            # prevote no
                            if vote_msg[1] != 1:
                                # print(pid, "get prevote no in round", r)
                                try:
                                    assert vote_msg[0] == Leader
                                    # assert (ecdsa_vrfy(PK2s[sender], digest_no, vote_msg[3]))
                                    # assert (PK1.verify_share(vote_msg[3], sender, digest_no) == 1)
                                except AssertionError:
                                    if logger is not None:
                                        logger.info("pre-vote no failed!")
                                    print("pre-vote no failed!")
                                    pass
                                prevote_no_shares[sender] = vote_msg[3]
                                if len(prevote_no_shares) == N - f - l:
                                    sigmas_no = tuple(list(prevote_no_shares.items())[:N - f - l])
                                    digest_no_no = hash(str((sid, Leader, r, 'vote')))
                                    vote = (Leader, 0, "bottom", sigmas_no, ecdsa_sign(SK2, digest_no_no))
                                    broadcast(('MVBA_ABA', r, r, ('vote', vote)))
                                    # print(pid, "vote no in round", r)
                                    # if pid ==3: print("VOTE 0")
                                    hasVoted = True

                            elif vote_msg[1] == 1:
                                try:
                                    assert vote_msg[0] == Leader
                                    # for (k, sig_k) in vote_msg[3]:
                                    #     assert ecdsa_vrfy(PK2s[k], hash(str((sid + 'SPBC' + str(Leader), vote_msg[2], "ECHO"))),
                                    #                       sig_k)
                                except AssertionError:
                                    if logger is not None: logger.info("pre-vote Signature failed!")
                                    # print("pre-vote Signature failed!")
                                    pass
                                pii = hash(str((sid + 'SPBC' + str(Leader), vote_msg[2], "FINAL")))
                                vote = (Leader, 1, vote_msg[2], vote_msg[3], ecdsa_sign(SK2, pii))
                                broadcast(('MVBA_ABA', r, r, ('vote', vote)))
                                # if pid ==3: print("VOTE 1")
                                hasVoted = True

                        # vote yes
                        if aba_tag == 'vote':
                            # if pid == 3: print("Receive VOTE from %d towards %d" % (sender, vote_msg[1]))
                            if vote_msg[1] == 1:
                                if vote_msg[0] != Leader:
                                    print("wrong Leader")
                                    if logger is not None: logger.info("wrong Leader")

                                hash_e = hash(str((sid + 'SPBC' + str(Leader), vote_msg[2], "ECHO")))
                                try:
                                    for (k, sig_k) in vote_msg[3]:
                                        assert ecdsa_vrfy(PK2s[k], hash_e,
                                                          sig_k)
                                    # assert PK1.verify_signature(vote_msg[3], PK1.hash_message(str((sid + 'SPBC' + str(Leader), vote_msg[2], "ECHO"))))
                                    assert ecdsa_vrfy(PK2s[sender],
                                                      hash(str((sid + 'SPBC' + str(Leader), vote_msg[2], "FINAL"))),
                                                      vote_msg[4])
                                except AssertionError:
                                    # if logger is not None: logger.info("vote Signature failed!")
                                    print("vote Signature failed!")
                                    # continue
                                    pass

                                vote_yes_shares[sender] = vote_msg[4]
                                vote_yes_msg = vote_msg[2]
                                # 2f+1 vote yes

                                # if pid == 3: print("++++++++++++++++++++++++++++++++++round %d smvba vote numbers YES: %d, NO: %d" %
                                #                    (r, len(vote_yes_shares), len(vote_no_shares) )
                                #                    )

                                if len(vote_yes_shares) == N - f - l:

                                    halt_msg = (
                                        Leader, 2, vote_msg[2], tuple(list(vote_yes_shares.items())[:N - f - l]))
                                    # broadcast(('MVBA_HALT', r, pid, ("halt", halt)))
                                    # print(pid, sid, "enough vote yes, halt here 3")
                                    if logger is not None: logger.info(
                                        "round %d smvba decide in vote yes %f" % (r, time.time()))

                                    halt_send.put_nowait(('MVBA_HALT', r, pid, ("halt", halt_msg)))

                                    hasOutputed = True
                                    okay_to_stop.set()
                                    start_wait_for_halt.set()
                                    return 1
                            # vote no
                            if vote_msg[1] == 0:
                                if vote_msg[0] != Leader:
                                    print("wrong Leader")
                                    if logger is not None: logger.info("wrong Leader")

                                hash_pre = hash(str((sid, Leader, r, 'pre')))
                                try:
                                    # vrify sigmas_no
                                    for (k, sig_k) in vote_msg[3]:
                                        assert ecdsa_vrfy(PK2s[k], hash_pre, sig_k)

                                except AssertionError:
                                    # if logger is not None: logger.info("vote no failed!")
                                    print(pid, "vote no failed! sigmas in round", r)
                                    pass

                                try:
                                    # vrify no_no
                                    digest_no_no = hash(str((sid, Leader, r, 'vote')))
                                    assert ecdsa_vrfy(PK2s[sender], digest_no_no, vote_msg[4])
                                except AssertionError:
                                    if logger is not None: logger.info("vote no failed!")
                                    print("vote no failed!, digest_no_no, in round", r)
                                    pass

                                vote_no_shares[sender] = vote_msg[4]

                                if len(vote_no_shares) == N - f - l:
                                    pis = tuple(list(vote_no_shares.items())[:N - f - l])
                                    # print(pis)
                                    # print(pid, sid, "n-f no vote, move to next round with in round", r + 1)
                                    if logger is not None:
                                        logger.info('n-f no vote move to round %d' % (int(r) + 1))
                                    r += 1
                                    # if pid == 1: print("==============r:", r, "====================")
                                    if my_spbc_input.empty() == False:
                                        my_spbc_input.get()
                                    my_spbc_input.put_nowait((my_msg, pis, r, 'no'))

                                    # print("put into ???a?????????")
                                    prevote_no_shares.clear()
                                    vote_yes_shares.clear()
                                    vote_no_shares.clear()
                                    okay_to_stop.set()
                                    # r = r % 10
                                    break
                            # both vote no and vote yes
                            if (len(vote_no_shares) > 0) and (len(vote_yes_shares) > 0):
                                # print("both vote no and vote yes, move to next round", r+1)
                                if logger is not None:
                                    logger.info('both vote yes and no, move to round %d' % (int(r)+1))
                                r += 1
                                my_spbc_input.put_nowait((vote_yes_msg[0], vote_msg[3], r, 'yn'))
                                # my_spbc_input.put_nowait(vote_yes_msg)

                                prevote_no_shares.clear()
                                vote_yes_shares.clear()
                                vote_no_shares.clear()
                                okay_to_stop.set()
                                # r = r % 10
                                break
                    except Exception as e:
                        traceback.print_exc(e)
                        continue

            gevent.spawn(vote_loop)
            okay_to_stop.wait()


    view_change_thred = gevent.Greenlet(views)
    view_change_thred.start()

    def recv_halt():
        nonlocal hasOutputed, r, decide, halt_recv

        while decide is not None and halt_recv is not None:
            gevent.sleep(0.0001)
            try:
                sender, halt = halt_recv.get_nowait()
                halt_tag, halt_msg = halt
                if halt_tag == 'halt':
                    hash_f = hash(str((sid + 'SPBC' + str(halt_msg[0]), halt_msg[2], "FINAL")))
                    try:
                        # print("-----------------", halt_msg)
                        for (k, sig_k) in halt_msg[3]:
                            assert ecdsa_vrfy(PK2s[k], hash_f, sig_k)
                    except AssertionError:
                        # if logger is not None: logger.info("vote Signature failed!")
                        # print("vote Signature failed!")
                        continue

                    # send(-2, ('MVBA_HALT', r, pid, ("halt", halt_msg)))
                    halt_send.put_nowait(('MVBA_HALT', r, pid, ("halt", halt_msg)))

                    # print(pid, sid, "decide in vaba")
                    decide(halt_msg[2][0])
                    hasOutputed = True
                    start_wait_for_halt.set()
                    okay_to_stop.set()
                    decide = None
                    halt_recv = None

                    if logger is not None: logger.info("round %d smvba decide in halt in %f second" % (r, time.time()-s_t))
                    break
                    # return 2
            except Exception as err:
                # traceback.print_exc()
                continue
        return 2

    def send_halt():
        while True:
            # gevent.sleep(0.0001)
            try:
                o = halt_send.get()
                (_, rx, pidx, (_, haltx)) = o
                broadcast(('MVBA_HALT', rx, pidx, ("halt", haltx)))
                break
            except Exception as err:
                traceback.print_exc()
                continue

    # print(pid, "start to kill thread in MVBA", sid)

    halt_recv_thred = gevent.Greenlet(recv_halt)
    halt_send_thred = gevent.Greenlet(send_halt)
    halt_recv_thred.start()
    halt_send_thred.start()
    halt_recv_thred.join()
    # gevent.sleep(2)
    halt_recv_thred.kill()

    halt_send_thred.join()
    gevent.sleep(0.01)
    recv_loop_thred.kill()
    vote_recvs = None
    #aba_recvs = None
    for item in aba_recvs:
       item = None

    spbc_recvs = None
    coin_recv = None
    halt_recv = None