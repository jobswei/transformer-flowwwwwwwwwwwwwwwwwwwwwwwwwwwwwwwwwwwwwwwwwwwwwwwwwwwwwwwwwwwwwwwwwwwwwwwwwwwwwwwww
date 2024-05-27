import torch
import torch.nn as nn
import torch.nn.functional as F
import numpy as np

from models.transformer_flow_blocks.DAT import DAttentionBaseline

class AttentionTD(nn.Module):
    def __init__(self, variable_dims:list[tuple[int]],) -> None:
        super().__init__()
        self.dat_blocks=nn.ModuleList()
        for i  in range(len(variable_dims)):
            for j in range(i):
                q_size=(variable_dims[i][-2],variable_dims[i][-1])
                kv_size=(variable_dims[j][-2],variable_dims[j][-1])
                c=variable_dims[i][-3]//2
                dat=DAttentionBaseline(q_size=q_size,kv_size=kv_size,n_heads=4,n_head_channels=c//4, n_groups=1,
                                attn_drop=0, proj_drop=0, stride=4,offset_range_factor=-1, 
                                use_pe=True,dwc_pe=False,no_off=False,fixed_pe=False,
                                ksize=4,log_cpb=False)
                self.dat_blocks.append(dat)
    def forward(self,hidden_variables:list[torch.Tensor],rev=False):
        b,c,h,w=hidden_variables[0].shape
        results=[]
        c1=hidden_variables[0].shape[1]//2
        c2=hidden_variables[0].shape[1]-c1
        num=0
        for i in range(len(hidden_variables)):
            res=hidden_variables[i].clone()
            for j in range(i):
                q=hidden_variables[i][:,:c1,...]
                kv=results[j][:,:c1,...] if rev else hidden_variables[j][:,:c1,...]
                attn,_,_=self.dat_blocks[num](q,kv)
                attn=torch.cat((torch.zeros_like(res)[:,:c2,...],attn),dim=1)
                res-=attn if rev else -1*attn
                num+=1
            results.append(res)
        return results,torch.zeros(len(hidden_variables),b).to(hidden_variables[0].device)


class AttentionBU(nn.Module):
    def __init__(self, variable_dims:list[tuple[int]],) -> None:
        super().__init__()
        variable_dims.reverse()
        self.dat_blocks=nn.ModuleList()
        for i  in range(len(variable_dims)):
            for j in range(i):
                q_size=(variable_dims[i][-2],variable_dims[i][-1])
                kv_size=(variable_dims[j][-2],variable_dims[j][-1])
                c=variable_dims[i][-3]//2
                dat=DAttentionBaseline(q_size=q_size,kv_size=kv_size,n_heads=4,n_head_channels=c//4, n_groups=1,
                                attn_drop=0, proj_drop=0, stride=4,offset_range_factor=-1, 
                                use_pe=True,dwc_pe=False,no_off=False,fixed_pe=False,
                                ksize=4,log_cpb=False)
                self.dat_blocks.append(dat)
    def forward(self,hidden_variables:list[torch.Tensor],rev=False):
        b,c,h,w=hidden_variables[0].shape
        results=[]
        c1=hidden_variables[0].shape[1]//2
        c2=hidden_variables[0].shape[1]-c1
        hidden_variables.reverse()
        num=0
        for i in range(len(hidden_variables)):
            res=hidden_variables[i].clone()
            for j in range(i):
                q=hidden_variables[i][:,:c1,...]
                kv=results[j][:,:c1,...] if rev else hidden_variables[j][:,:c1,...]
                attn,_,_=self.dat_blocks[num](q,kv)
                attn=torch.cat((torch.zeros_like(res)[:,:c2,...],attn),dim=1)
                res-=attn if rev else -1*attn
                num+=1
            results.append(res)
        results.reverse()
        hidden_variables.reverse()
        return results,torch.zeros(len(hidden_variables),b).to(hidden_variables[0].device)

class AttentionAll(nn.Module):
    def __init__(self,variable_dims:list[tuple[int]]) -> None:
        super().__init__()
        self.attnTD=AttentionTD(variable_dims)
        self.attnBU=AttentionBU(variable_dims)
    def forward(self,hidden_variables:list[torch.Tensor],rev=False) -> tuple[list[torch.Tensor],float]:
        if rev:
            bu_r,log_jac_bu=self.attnBU(hidden_variables,rev=rev)
            td_r,log_jac_td=self.attnTD(bu_r,rev=rev)
            return td_r,(log_jac_bu+log_jac_td)
        resTD,log_jac_td=self.attnTD(hidden_variables,rev=rev)
        resBU,log_jac_bu=self.attnBU(resTD,rev=rev)
        return resBU,(log_jac_bu+log_jac_td)
def PLU_matrix(dim):
    import scipy
    np_w=scipy.linalg.qr(np.random.randn(dim,dim))[0].astype("float32") # 通过QR分解得到正交矩阵

    np_p,np_l,np_u = scipy.linalg.lu(np_w)
    np_s=np.diag(np_u)
    np_sign_s=np.sign(np_s)
    np_log_s=np.log(np.abs(np_s))
    np_u=np.triu(np_u,k=1)
    
    dtype = torch.float64

    p=torch.tensor(np_p,requires_grad=False).to(dtype)
    s=torch.tensor(np_s,requires_grad=False).to(dtype)
    sign_s=torch.tensor(np_sign_s,requires_grad=False).to(dtype)
    log_s=torch.tensor(np_log_s,requires_grad=True).to(dtype)
    l=torch.tensor(np_l,requires_grad=True).to(dtype)
    u=torch.tensor(np_u,requires_grad=True).to(dtype)

    # w_shape=[dim,dim]
    # l_mask=torch.tril(torch.ones(w_shape,dtype=dtype),diagonal=-1)
    # l=l*l_mask+torch.eye(w_shape,dtype=dtype)
    # u=u*torch.transpose(l_mask)+torch.diag(sign_s*torch.exp(log_s))

    return p,l,u,sign_s,log_s

class FeedForward(nn.Module):
    def __init__(self, input_dim, output_dim):
        super(FeedForward, self).__init__()
        assert input_dim==output_dim
        self.p,self.l,self.u,self.sign_s,self.log_s=tuple(map(lambda x:nn.Parameter(x),PLU_matrix(input_dim)))
        self.p.requires_grad=False
        self.sign_s.requires_grad=False

        # self.linear = nn.Linear(input_dim, output_dim)

    def forward(self, x:torch.Tensor,rev=False) -> tuple[torch.Tensor,float]:
        b,c,h,w=x.shape
        x=x.permute(0,2,1,3)

        log_jac=torch.repeat_interleave(torch.sum(self.log_s),repeats=b)*h*w

        # 优化之后，plu就不是标准的上、下三角了，要变化一下
        w_shape=tuple(self.l.shape)
        dtype=self.l.dtype
        device=self.l.device
        l_mask=torch.tril(torch.ones(w_shape),diagonal=-1).to(device)
        l=self.l*l_mask+torch.eye(w_shape[0],dtype=dtype).to(device)
        u=self.u*torch.transpose(l_mask,0,1)+torch.diag(self.sign_s*torch.exp(self.log_s))

        if rev:
            p_inv=torch.inverse(self.p)
            l_inv=torch.inverse(l,)
            u_inv=torch.inverse(u)
            w_inv=torch.matmul(u_inv,torch.matmul(l_inv,p_inv)).to(x.dtype)
            x_rev=torch.matmul(w_inv,x)
            x_rev=x_rev.permute(0,2,1,3)
            return x_rev,-log_jac

        w=torch.matmul(self.p,torch.matmul(l,u)).to(x.dtype)
        x=torch.matmul(w,x)
        x=x.permute(0,2,1,3)
        return x,log_jac

class Normalize(nn.Module):
    def __init__(self,input_dim):
        super().__init__()
        self.norm=nn.BatchNorm2d(input_dim)
        self._update_para()
    def forward(self,x,rev=False):
        b,c,h,w=x.shape
        log_jac=torch.repeat_interleave(torch.sum(-0.5*torch.log(self.paras["var"] + self.paras["eps"]))*h*w*c,repeats=b).to(x.device)
        if rev:
            x_r = x * torch.sqrt(self.paras["var"] + self.paras["eps"]).view(1,c,1,1) + self.paras["mean"].view(1,c,1,1)
            return x_r,-log_jac
        self._update_para()
        out=self.norm(x)
        return out,log_jac
    def _update_para(self):
        self.paras={"var":self.norm.running_var.detach().clone(),"eps":self.norm.eps,"mean":self.norm.running_mean.detach().clone()}

class TransformFlowBlock(nn.Module):
    def __init__(self,variable_dims:tuple[tuple[int]]) -> None:
        super().__init__()
        self.variable_dims=list(variable_dims[0])
        self.attention=AttentionAll(self.variable_dims)
        self.ffn=FeedForward(self.variable_dims[0][1],self.variable_dims[0][1])
        self.norm=Normalize(self.variable_dims[0][1])
    def output_dims(self,dim_in):
        return dim_in
    def forward(self,hidden_variables:list[torch.Tensor],jac:bool=True,rev:bool=False) -> list[torch.Tensor]:
        results=[]
        log_jacs=torch.zeros(len(hidden_variables),hidden_variables[0].shape[0]).to(hidden_variables[0].device)
        if rev:
            for num,attn in enumerate(hidden_variables):
                attn,norm_log_jac=self.norm(attn,rev=True)
                attn,ffn_log_jac=self.ffn(attn,rev=True)
                results.append(attn)
                log_jacs[num]+=(ffn_log_jac+norm_log_jac)
                log_jacs[num]+=(norm_log_jac)
            results,attn_log_jac=self.attention(results,rev=True)
            log_jacs+=attn_log_jac
            return results,log_jacs
        
        attns,attn_log_jac=self.attention(hidden_variables)
        log_jacs+=attn_log_jac
        for num,attn in enumerate(attns):
            attn,ffn_log_jac=self.ffn(attn)
            attn,norm_log_jac=self.norm(attn)
            results.append(attn)
            log_jacs[num]+=(ffn_log_jac+norm_log_jac)
            log_jacs[num]+=(norm_log_jac)
        return results,log_jacs


def check_reverse(cls):
    hidden_variables=[torch.rand(4,256,16,16),torch.rand(4,256,32,32),torch.rand(4,256,64,64)]
    dims=[i.shape for i in hidden_variables]
    trans=cls(dims)
    res,jac=trans(hidden_variables)
    res_r,jac_r=trans(res,rev=True)
    return [torch.all(torch.abs(hidden_variables[i]-res_r[i])<1e-2) for i in range(len(res))]

if __name__=="__main__":
    # hidden_variables=[torch.rand(4,256,16,16),torch.rand(4,256,32,32),torch.rand(4,256,64,64)]
    # dims=[i.shape for i in hidden_variables]
    # ffn=Normalize(256)
    # a,_=ffn(hidden_variables[1])
    # a_r,_=ffn(a,rev=True)
    # print(torch.all(torch.abs(a_r-hidden_variables[1])<1e-2))
    # print(check_reverse(TransformFlowBlock))
    # print()
    ffn=FeedForward(5,5)
    x=torch.rand(4,5,16,16)
    ffn(x)
    print()