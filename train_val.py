import torch
import torch.nn as nn
import torch.optim as optim
import os
import argparse
from torch.backends import cudnn
from torch.utils.data import DataLoader

from dataset import CTDataset
from models import BRDNet
from torch.optim.lr_scheduler import ReduceLROnPlateau
from utils.logger import setup_logger
import logging


def get_args():
    parser = argparse.ArgumentParser()
    parser.add_argument('--mode', type=str, default='train', help="train | test")
    parser.add_argument('--pretrained', type=str, default='', help="pretrained model")

    parser.add_argument('--train_path', type=str, default='data/image_path/train.txt')
    parser.add_argument('--val_path', type=str, default='data/image_path/val.txt')
    parser.add_argument('--save_dir', type=str, default='./ckpt/1')
    parser.add_argument('--result_fig', type=bool, default=True)
    parser.add_argument('--transform', type=bool, default=False)

    # if patch training, batch size is (--patch_n x --batch_size)
    parser.add_argument('--patch_n', type=int, default=10)
    parser.add_argument('--patch_size', type=int, default=50)
    parser.add_argument('--batch_size', type=int, default=16)

    parser.add_argument('--num_epochs', type=int, default=50)
    parser.add_argument('--print_iters', type=int, default=20)
    parser.add_argument('--decay_iters', type=int, default=3000)
    parser.add_argument('--save_interval', type=int, default=1)
    parser.add_argument('--test_interval', type=int, default=1)

    parser.add_argument('--lr', type=float, default=1e-3)
    parser.add_argument('--device', type=str, default='cpu')
    parser.add_argument('--num_workers', type=int, default=2)
    parser.add_argument('--multi_gpu', type=bool, default=False)
    args = parser.parse_args()
    return args


def train(dataloader, model, loss_func, optimizer, epoch, args, logger):
    args.device = 'cpu'
    total_loss = 0.0
    model.train()
    for batch_i, (inp_data, gt_data, _) in enumerate(dataloader):
        if args.patch_size:
            inp_data = inp_data.view(-1, 1, args.patch_size, args.patch_size)
            gt_data = gt_data.view(-1, 1, args.patch_size, args.patch_size)
        inp_data = inp_data.to(args.device)
        gt_data = gt_data.to(args.device)
        pred = model(inp_data)
        loss = loss_func(pred, gt_data)
        optimizer.zero_grad()
        loss.backward()
        optimizer.step()

        total_loss += loss.item()
        if batch_i % args.print_iters == 0:
            logger.info(
                "Epoch: {} Batch: {}/{} | train_loss: {:.6f} | Mean loss: {:.6f} lr: {}".format(epoch, batch_i + 1,
                                                                                                len(dataloader),
                                                                                                loss.item(),
                                                                                                total_loss / (
                                                                                                        batch_i + 1),
                                                                                                optimizer.param_groups[
                                                                                                    0]['lr']))
        # if batch_i > 20: break
    print('')
    return total_loss / (batch_i + 1)


def val(dataloader, model, loss_func, epoch, args, logger):
    args.device = 'cpu'
    total_loss = 0.0
    total_psnr = 0.0
    total_org_psnr = 0.0
    model.eval()
    with torch.no_grad():
        for batch_i, (inp_data, gt_data, _) in enumerate(dataloader):
            inp_data = inp_data.view(-1, 1, args.patch_size, args.patch_size)
            gt_data = gt_data.view(-1, 1, args.patch_size, args.patch_size)
            pred = model(inp_data)
            loss = loss_func(pred, gt_data)
            org_mse = loss_func(inp_data, gt_data)
            # batch size 1
            psnr = 10 * torch.log10(1 / loss).item()
            org_psnr = 10 * torch.log10(1 / org_mse).item()
            total_loss += loss.item()
            total_psnr += psnr
            total_org_psnr += org_psnr
            if batch_i % args.print_iters == 0:
                logger.info(
                    "Epoch: {} Batch: {}/{} | val_loss: {:.6f} | Mean loss: {:.6f}, psnr: {:.2f}, mean psnr: {:.2f}, org_psnr: {:.2f}, mean org_psnr: {:.2f}".format(
                        epoch, batch_i + 1, len(dataloader), loss.item(), total_loss / (batch_i + 1), psnr,
                               total_psnr / (batch_i + 1), org_psnr, total_org_psnr / (batch_i + 1)))
            # if batch_i > 20: break
    logger.info('mean psnr: {}'.format(total_psnr / len(dataloader)))
    logger.info('mean org psnr: {}'.format(total_org_psnr / len(dataloader)))
    logger.info('mean loss: {}'.format(total_loss / len(dataloader)))
    return total_loss / len(dataloader)


def main():
    args = get_args()
    if not os.path.exists(args.save_dir):
        os.makedirs(args.save_dir)
    device = 'cpu'

    logger = setup_logger("brdnet", args.save_dir, 0)
    logger.info("Using device {}".format(device))
    logger.info(args)

    train_data = CTDataset(data_path=args.train_path, patch_n=args.patch_n, patch_size=args.patch_size)
    val_data = CTDataset(data_path=args.val_path, patch_n=args.patch_n, patch_size=args.patch_size)
    train_loader = DataLoader(dataset=train_data, batch_size=1, shuffle=True, num_workers=args.num_workers)
    val_loader = DataLoader(dataset=val_data, batch_size=1, shuffle=False, num_workers=args.num_workers)
    model = BRDNet()
    if args.pretrained != '':
        model.load_state_dict(torch.load(args.pretrained))
    model.to(device)

    optimizer = optim.Adam(model.parameters(), args.lr)
    lr_scheduler = ReduceLROnPlateau(optimizer, mode='min', factor=0.3, verbose=True, patience=6, min_lr=1E-7)

    loss_func = nn.MSELoss()

    best_loss = float('inf')
    for epoch in range(1, args.num_epochs + 1):
        logger.info('epoch: {}/{}'.format(epoch, args.num_epochs))
        t_loss = train(train_loader, model, loss_func, optimizer, epoch, args, logger)
        v_loss = val(val_loader, model, loss_func, epoch, args, logger)
        lr_scheduler.step(v_loss)

        if v_loss < best_loss:
            best_loss = v_loss
            torch.save(model.state_dict(), '{}/model_best.pth'.format(args.save_dir))

        if (epoch) % args.save_interval == 0:
            torch.save(model.state_dict(), "{}/model_checkpoint_{}.pth".format(args.save_dir, epoch))
        # if epoch > 10: break

    logger.info('done')


if __name__ == '__main__':
    main()
