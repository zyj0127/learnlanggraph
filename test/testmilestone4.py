import sys
import  io
import threading
from PIL import Image as PILImage
from pathlib import Path

from langchain_core.messages import HumanMessage

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.append(str(PROJECT_ROOT))

from agent.graph_builder import hr_agent_app

class SessinManger:
    def __init__(self, timeout_sends:int=5):
        self.timeout_sends = timeout_sends
        self.timer=0
        self.current_thread_id = None
        self.current_uid = None

    def trigger_summary(self):
        """倒计时结束触发的方法：向Graph发送指令"""
        if  not self.current_thread_id:
            return

        config={'configurable':{'thread_id':self.current_thread_id}}
        idle_trigger_state={
            'messages':[HumanMessage("__SYS_IDLE_TIMEOUT__")],
            'current_uid':self.current_uid,
        }
        print(f'[后台守护线程]检测到用户{self.current_uid}闲置超过{self.timeout_sends}秒触发自动总结')

        for event in hr_agent_app.stream(idle_trigger_state,config,stream_mode='values'):
            last_message=event['messages'][-1]
            if last_message.type=='ai' and not last_message.tool_calls:
                print(f'{last_message.content}')
            print('继续提问')

    def reset_timer(self):
       if self.timer:
           self.timer.cancel()

       self.timer = threading.Timer(self.timeout_sends, self.trigger_summary)


       self.timer.start()

    def chat(self,uid:str,thread_id:str,question:str):
        self.current_thread_id=thread_id
        self.current_uid=uid

        if self.timer:
            self.timer.cancel()
        print(f'[uid]{uid}提问{question}')
        config={'configurable':{'thread_id':self.current_thread_id}}

        state={
            'messages':[HumanMessage(content=question)],
            'current_uid':uid,
            'loop_state':0,
        }

        for event in hr_agent_app.stream(state,config,stream_mode='values'):
            last_msg=event['messages'][-1]
            if isinstance(last_msg,HumanMessage):
                continue
            if last_msg.type=='ai' and not last_msg.tool_calls:
                print(f'[ai答复]{last_msg.content}')

        self.reset_timer()

if __name__ == '__main__':
    print('========多轮记忆与异步超时总结测试========')
    session=SessinManger(timeout_sends=30)

    session.chat(uid='1001',thread_id='session_1001_a',question='你好，我是张三')
    import time
    time.sleep(6)
    session.chat(uid='1001',thread_id='session_1001_a',question='我还有多少天年假')
    print('聊天结束，用户离开电脑，开始测试30s后闲置自动总结，请不要操作')

    try:
        time.sleep(89)
    except KeyboardInterrupt:
        pass
    finally:
        if session.timer:
            session.timer.cancel()
            print('======')



