#模型下载

from modelscope import  snapshot_download

model_id='BAAI/bge-small-zh-v1.5'
model_dir=snapshot_download(
    model_id=model_id,
    cache_dir='.local_models',
    revision='master',
)
print('pass')