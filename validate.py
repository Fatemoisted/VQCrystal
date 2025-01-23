import argparse
import os
from pickle import NONE
import shutil
import sys
import time
import warnings
from random import sample
import numpy as np
import torch
import torch.nn as nn
import torch.optim as optim
from sklearn import metrics
from torch.autograd import Variable
from torch.optim.lr_scheduler import MultiStepLR
from sklearn.metrics import r2_score
import random
from torch.optim.lr_scheduler import CosineAnnealingLR
import pdb
import yaml
from tqdm import tqdm
import wandb
from data import CIFData, collate_pool, get_train_val_test_loader
from model.model2 import GraphVAE
from utils import *
from pymatgen.analysis.structure_matcher import StructureMatcher
from pymatgen.core.structure import Structure
import warnings
import warnings
from pathlib import Path
from itertools import islice
warnings.filterwarnings("ignore")

parser = argparse.ArgumentParser(description="basic argument for crystalvae")
# about data
parser.add_argument("--data_path", default="./data/perov_5", type=str)
parser.add_argument("--config_path", default="./config/config_perov_5.yaml", type=str)
parser.add_argument("--wandb", default=False, type=bool)
parser.add_argument("--finetuning", default=False, type=bool)
parser.add_argument("--save", default=True, type=bool)


def set_seed(seed):
    random.seed(seed)
    np.random.seed(seed)
    os.environ['PYTHONHASHSEED'] = str(seed)
    torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed(seed)
        torch.cuda.manual_seed_all(seed)
    
def validate(loader, model, device, use_wandb):
    print("--------------------------------------------------------")
    print("Validation:")
    model.eval()
    val_metrics = {
        "val_loss": 0.0,
        "val_V_loss": 0.0,
        "val_P_loss": 0.0,
        "val_commitment_loss": 0.0,
        "val_lattice_loss": 0.0,
        "val_property_loss": 0.0,
        "val_atom_acc": 0.0,
        "val_match_acc": 0.0,
        "val_rms_dist": 0.0,
        "val_match_acc_with_lattice": 0.0,
        "val_rms_dist_with_lattice": 0.0,
    }
    total_match=0   
    num_batches =0
    with torch.no_grad():
        with tqdm(loader) as pbar:
            for batch in pbar:
                num_batches+=1
                batch_dict, _ = batch
                atom_fea = batch_dict['atom_fea']
                num=atom_fea.mask.size()[0]
                A = batch_dict['ad_matrix']
                coords = batch_dict['frac_coords_tensor']
                lattice = batch_dict['lattice']
                crystals = batch_dict['crystals']
                nbr_fea = batch_dict['nbr_fea']
                nbr_fea_idx = batch_dict['nbr_fea_idx']
                crystal_atom_idx = batch_dict['crystal_atom_idx']
                properties = batch_dict['property'].to(device)
                F = torch.cat((atom_fea.data, coords.data), dim=2)
                flat_mask = atom_fea.mask
                F = F.to(device)
                A = A.data.to(device)
                nbr_fea, nbr_fea_idx = nbr_fea.to(device), nbr_fea_idx.to(device)
                crystal_atom_idx = [crys_idx.to(device) for crys_idx in crystal_atom_idx]
                flat_mask = flat_mask.to(device)
                lattice = lattice.to(device)
                output = model.evaluation_forward(
                    F, A, flat_mask, lattice, nbr_fea, nbr_fea_idx, crystal_atom_idx, properties, crystals
                )
                val_metrics["val_loss"] += output["total_loss"]
                val_metrics["val_V_loss"] += output["V_loss"]
                val_metrics["val_P_loss"] += output["P_loss"]
                val_metrics["val_commitment_loss"] += output["commitment_loss"]
                val_metrics["val_lattice_loss"] += output["lattice_loss"]
                val_metrics["val_property_loss"] += output["property_loss"]
                val_metrics["val_atom_acc"] += output["atom_acc"]               
                val_metrics["val_match_acc_with_lattice"] += output["match_accuracy_with_lattice"]
                total_match+=output["rms_dist_with_lattice"][1]
                val_metrics["val_rms_dist_with_lattice"] += output["rms_dist_with_lattice"][0]
                pbar.set_postfix({"atom":output["atom_acc"].item(),"match":output["match_accuracy_with_lattice"]})
                
                
    for key in val_metrics:
        if 'rms' not in key:     
            val_metrics[key] /= num_batches
        else:
            if total_match!=0:
                val_metrics[key] /= total_match
            else:
                val_metrics[key]=0

    # Print validation results
    print(f"Validation Loss: {val_metrics['val_loss']:.4f}, V Loss: {val_metrics['val_V_loss']:.4f}, "
          f"P Loss: {val_metrics['val_P_loss']:.4f}, Commitment Loss: {val_metrics['val_commitment_loss']:.4f}, "
          f"Lattice Loss: {val_metrics['val_lattice_loss']:.4f}, Property Loss: {val_metrics['val_property_loss']:.4f}\n"
          f"Atom Acc: {val_metrics['val_atom_acc']:.4f},  Match Acc Lattice: {val_metrics['val_match_acc_with_lattice']:.4f},RMS Lattice: {val_metrics['val_rms_dist_with_lattice']:.4f} ")

    # Log to WandB
    if use_wandb:
        wandb.log(val_metrics)

    return val_metrics


if __name__ == "__main__":
    args = parser.parse_args()
    name=Path(args.data_path).name
    with open(args.config_path, "r") as f:
        config = yaml.load(f, Loader=yaml.FullLoader)
    config["cuda"] = torch.cuda.is_available()
    config["data_path"] = args.data_path
    device = torch.device("cuda:0" if config["cuda"] else "cpu")
    if args.wandb:
        wandb.login()
        wandb.init(project="crystalvae", config=config)
        config = wandb.config
    set_seed(config["random_seed"])
    dataset = CIFData(config["data_path"], keep=False, max_N=config["k"],proper=config.get("property", 'formation_energy'),force=False)
    element_counter = dataset.element_counter
    normalizer = dataset.norm
    normalizer_properties=dataset.norm_properties
    train_loader, val_loader, test_loader = get_train_val_test_loader(
        dataset=dataset,
        collate_fn=collate_pool,
        batch_size=config["batch_size"],
        train_ratio=config["train_ratio"],
        num_workers=config["workers"],
        val_ratio=config["val_ratio"],
        test_ratio=config["test_ratio"],
        pin_memory=False,
        train_size=NONE,
        val_size=NONE,
        test_size=NONE,
        return_test=True,
    )
    # loss = FocalLoss(
    #     class_num=config["class_dim"], alpha=convert_counter_to_alpha(element_counter)
    # )
    loss = FocalLoss(
        class_num=config["class_dim"]
    )
    model_config = {
        "loss": loss,
        "class_dim": config["class_dim"],
        "pos_dim": config["pos_dim"],
        "lattice_dim": config["lattice_dim"],
        "hidden_dim": config["hidden_dim"],
        "latent_dim": config["latent_dim"],
        "k": config["k"],
        "encoder1_dim": config["encoder1_dim"],
        "num_encoder2_layers": config["num_encoder2_layers"],
        "decoder2_dims": config["decoder2_dims"],
        "decoder1_dims": config["decoder1_dims"],
        "decoder_lattice_dims": config["decoder_lattice_dims"],
        "decoder_lattice_dims2": config["decoder_lattice_dims2"],
        "codebooksize1": config["codebooksize1"],
        "codebooksize2": config["codebooksize2"],
        "codebooksizel": config["codebooksizel"],
        "num_quantizers1": config["num_quantizers1"],
        "num_quantizers2": config["num_quantizers2"],
        "num_quantizersl": config["num_quantizersl"],
        "sample_codebook_temp": config["sample_codebook_temp"],
        "normalizer": normalizer,
        "normalizer_properties":normalizer_properties,
    }

    model = GraphVAE(**model_config).to(device)
    
    print("loading model weights........")
    model.load_state_dict(
        torch.load(f"./ckpt/{name}/{name}_model_weights_{config['codebooksize1']}_{config['num_quantizers1']}_.pth")["model_state_dict"], strict=False
    )
    print("Load OK!")
    
    val_metrics = validate(val_loader, model, device, use_wandb=args.wandb)
    

    wandb.finish()
